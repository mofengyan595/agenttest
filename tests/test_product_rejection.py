"""A design proposal rejected before its request must end as NEEDS_REVIEW, never FINISHED."""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/rw_lifecycle") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import authority  # noqa: E402

from agenttest.diagnostics import Layer, ValidationFailure  # noqa: E402
from agenttest.generic_intake import GenericSetup  # noqa: E402
from agenttest.project_intake import IntakeInput  # noqa: E402
from agenttest.report import ModelCallStats, ReportStore  # noqa: E402
from agenttest.task_service import TaskService  # noqa: E402
from agenttest.task_store import TaskStore  # noqa: E402

TOKENS = {f"AGENTTEST_TARGET_RW_{alias.upper()}_TOKEN": f"preparation-{alias}"
          for alias in ("author", "editor", "auditor", "outsider")}


def service(root, design):
    pytest.importorskip("langgraph")
    return TaskService({}, TaskStore(root / "tasks.sqlite3"), ReportStore(root / "reports"),
                       lambda _: None, designer_factory=lambda _: design, generic_intake=True,
                       environ=TOKENS, model_status=lambda: "CONFIGURED_MODEL")


async def settled(svc, row):
    for _ in range(200):
        current = svc.db.get(row["id"], "operator")
        if current["status"] in {"FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}:
            return current
        await asyncio.sleep(0.01)
    pytest.fail("Task did not terminate")


class Rejected:
    """Raises exactly like the product did before sending the design request."""

    stats = ModelCallStats(calls=0)

    async def generate(self, context):  # noqa: ARG002
        raise ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_ENUM", "Request value outside enum")


@pytest.mark.asyncio
async def test_design_rejection_is_reported_and_never_finished(tmp_path):
    svc = service(tmp_path, Rejected())
    saved = svc.intake.save(IntakeInput(name="rejection", document=authority.document(),
                                        confirmed=True,
                                        generic=GenericSetup.model_validate(authority.setup())),
                            "operator")
    row = await svc.submit("operator", saved["id"],
                           "先用 author 身份创建名为 first 的条目；读取该条目确认返回 200。",
                           "rejection-design-0001")
    current = await settled(svc, row)
    summary = current["summary"]

    assert current["status"] == "NEEDS_REVIEW"
    assert summary["reason"].startswith("方案未通过校验")
    assert summary["complete"] is False and summary["checks_passed"] is False
    assert summary["diagnostic"]["code"] == "OPENAPI_VALUE_ENUM"
    assert summary["diagnostic"]["layer"] == "openapi"
    assert [record["status"] for record in summary["design_records"]] == ["REJECTED"]
    assert summary["design_records"][-1]["reason"] == "OPENAPI_VALUE_ENUM"

    report = svc.reports.read(summary["report_id"])
    assert report.outcome.value == "NEEDS_REVIEW"
    assert report.reason == "OPENAPI_VALUE_ENUM"
    assert report.diagnostic.code == "OPENAPI_VALUE_ENUM"
    assert report.steps == [] and report.plan_valid is False
    # A rejected proposal never sent anything, so no usage is left unknown.
    assert svc.db.model_usage_blocked() is False


@pytest.mark.asyncio
async def test_unrejected_round_still_finishes(tmp_path):
    """The new branch must not turn ordinary finished rounds into NEEDS_REVIEW."""

    class Quiet:
        stats = ModelCallStats(calls=1, input_tokens=1, output_tokens=1, total_tokens=2)

        async def generate(self, context):
            return '{"interpretation": "无需测试", "relevant": false, "cases": [], ' \
                   '"questions": [], "not_covered": ["本轮没有需要执行的接口动作。"], ' \
                   '"suggestions": []}'

    svc = service(tmp_path, Quiet())
    saved = svc.intake.save(IntakeInput(name="quiet", document=authority.document(),
                                        confirmed=True,
                                        generic=GenericSetup.model_validate(authority.setup())),
                            "operator")
    row = await svc.submit("operator", saved["id"], "读取一条已批准条目。", "quiet-round-0001")
    current = await settled(svc, row)
    assert current["status"] == "FINISHED"
    assert "方案未通过校验" not in (current["summary"].get("reason") or "")
