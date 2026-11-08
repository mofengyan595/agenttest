"""Small pre-paid scorer/fixture checks; no model, external service or paid API."""

import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # The frozen round-3 harness is a repository script, not an installed package.
    sys.path.insert(0, str(ROOT))

from scripts.round3_ab.fixture import create_fixture, tasks  # noqa: E402
from scripts.round3_ab.grade import evaluate  # noqa: E402


def sample(repeats=1):
    expected = dict(operation="read", role=None, status=200, values={"query": {"n": 2}}, fields=[])
    item = dict(
        rubric=dict(
            steps=[deepcopy(expected) for _ in range(repeats)],
            gaps=[],
            order=list(range(repeats)) if repeats > 1 else [],
            bindings=[],
        )
    )
    steps, nodes, audits = [], [], []
    for i in range(repeats):
        steps.append(
            dict(
                step_id=f"s{i}",
                operation_id="read",
                method="GET",
                path_template="/read",
                request_evidence=dict(role=None, values={"query": {"n": 2}}),
                outcome="PASS",
                attempts=[dict(status_code=200, response_sha256=f"h{i}")],
                checks=[dict(kind="http_status", passed=True, expected=[200], actual=200)],
            )
        )
        nodes.append(
            dict(
                id=f"s{i}",
                operation_id="read",
                role=None,
                policy_id="p",
                query={"n": dict(value=2, from_step=None, select="$")},
            )
        )
        audits.append(
            dict(
                sequence=i,
                operation_id="read",
                method="GET",
                path="/read",
                role=None,
                values={"query": {"n": 2}},
                status=200,
                response={},
                response_sha256=f"h{i}",
            )
        )
    report = dict(
        run_id="r",
        steps=steps,
        plan_structure=[dict(step_id=n["id"], dependencies={}) for n in nodes],
    )
    observations = [dict(kind="execution_plan", run_id="r", plan=dict(steps=nodes))]
    authority = dict(
        operations={"read": dict(method="GET", path="/read")},
        policies=[
            dict(
                id="p",
                operation_id="read",
                roles=[None],
                assertions=dict(
                    reviewed=True, source="Operator", status=dict(codes=[200]), fields=[]
                ),
                binding_rules=[dict(location="query", name="n", literal_schema={"enum": [2]})],
            )
        ],
    )
    return item, {}, [report], authority, audits, observations


def test_honest_success_and_distinct_repeat_are_verified():
    for count in (1, 2):
        result = evaluate(*sample(count))
        assert result["full_execution"] and result["evidence_integrity"]


def test_zero_request_global_rejection_is_a_false_refusal_without_questions():
    args = list(sample())
    args[1] = dict(design_records=[dict(status="REJECTED", group_id="focus")])
    args[2], args[4], args[5] = [], [], []
    result = evaluate(*args)
    assert result["false_refusal"] and result["silent_obligations"] == 3
    assert not result["full_execution"]


@pytest.mark.parametrize(
    "fault",
    [
        "repeat",
        "wrong_role",
        "wrong_value",
        "planned_only",
        "false_pass",
        "extra_request",
        "missing_audit",
    ],
)
def test_bad_evidence_never_scores_as_complete(fault):
    args = list(sample(2 if fault == "repeat" else 1))
    if fault == "repeat":
        args[2][0]["steps"].pop()
        args[4].pop()
    elif fault == "wrong_role":
        args[2][0]["steps"][0]["request_evidence"]["role"] = "reviewer"
        args[4][0]["role"] = "reviewer"
    elif fault == "wrong_value":
        args[2][0]["steps"][0]["request_evidence"]["values"]["query"]["n"] = 3
        args[4][0]["values"]["query"]["n"] = 3
    elif fault == "planned_only":
        args[2][0]["steps"][0]["attempts"] = []
        args[4] = []
    elif fault == "false_pass":
        args[2][0]["steps"][0]["attempts"][0]["status_code"] = 403
        args[4][0]["status"] = 403
    elif fault == "extra_request":
        args[4].append(dict(args[4][0], sequence=99))
    else:
        args[4] = []
    assert not evaluate(*args)["full_execution"]


def test_permission_hold_is_actor_scoped_and_source_backed():
    args = list(sample())
    gap = dict(kind="permission", operation="read", role="reviewer", source="reviewer")
    args[0]["rubric"]["gaps"] = [gap]
    args[1] = dict(
        focus=dict(
            expectation_questions=[
                dict(
                    operation_id="read",
                    role="reviewer",
                    kind="permission",
                    source_quote="请确认 reviewer 权限",
                )
            ]
        ),
        questions=[
            dict(operation_id="read", role="reviewer", kind="permission", status_choices=[403, 404])
        ],
    )
    good = evaluate(*args)
    assert good["correct_hold"] and good["pending_scope_violations"] == 0
    args[1]["questions"][0]["role"] = "owner"
    assert not evaluate(*args)["correct_hold"]


def test_two_permission_operations_cannot_be_collapsed_to_one_question():
    args = list(sample())
    args[0]["rubric"]["gaps"] = [
        dict(kind="permission", operation=op, role="reviewer", source="reviewer")
        for op in ("read", "another")
    ]
    args[1] = dict(
        focus=dict(
            expectation_questions=[
                dict(
                    operation_id="read",
                    role="reviewer",
                    kind="permission",
                    source_quote="reviewer 权限",
                )
            ]
        ),
        questions=[
            dict(operation_id="read", role="reviewer", kind="permission", status_choices=[403, 404])
        ],
    )
    assert not evaluate(*args)["correct_hold"]


def dynamic_sample():
    args = list(sample())
    source = dict(operation="source", role="owner", status=200, values={}, fields=[])
    args[0]["rubric"]["steps"].insert(0, source)
    args[0]["rubric"]["bindings"] = [
        dict(
            producer="source",
            consumer="read",
            source_role="owner",
            role=None,
            location="query",
            name="n",
            selector="$.link.id",
        )
    ]
    args[2][0]["steps"].insert(
        0,
        dict(
            step_id="producer",
            operation_id="source",
            method="GET",
            path_template="/source",
            request_evidence=dict(role="owner", values={}),
            outcome="PASS",
            attempts=[dict(status_code=200, response_sha256="source")],
            checks=[dict(kind="http_status", passed=True, expected=[200], actual=200)],
        ),
    )
    args[2][0]["plan_structure"][0]["dependencies"] = {"query.n": "producer"}
    args[2][0]["plan_structure"].insert(0, dict(step_id="producer", dependencies={}))
    args[3]["operations"]["source"] = dict(method="GET", path="/source")
    args[3]["policies"].append(
        dict(
            id="src",
            operation_id="source",
            roles=["owner"],
            assertions=dict(reviewed=True, source="Operator", status=dict(codes=[200]), fields=[]),
            binding_rules=[],
        )
    )
    args[3]["policies"][0]["binding_rules"] = [
        dict(
            location="query",
            name="n",
            source_policy_id="src",
            source_selector="$.link.id",
            role_relation="unauthenticated",
        )
    ]
    args[4][0]["sequence"] = 2
    args[4].insert(
        0,
        dict(
            sequence=1,
            operation_id="source",
            method="GET",
            path="/source",
            role="owner",
            values={},
            status=200,
            response={"link": {"id": 2}},
            response_sha256="source",
        ),
    )
    args[5][0]["plan"]["steps"][0]["query"]["n"] = dict(
        value=None, from_step="producer", select="$.link.id"
    )
    args[5][0]["plan"]["steps"].insert(
        0, dict(id="producer", operation_id="source", role="owner", policy_id="src")
    )
    return args


@pytest.mark.parametrize("fault", ["none", "literal", "selector", "source_policy", "source_role"])
def test_actual_source_binding_must_match_selector_policy_identity_and_response(fault):
    args = dynamic_sample()
    consumer = args[5][0]["plan"]["steps"][1]
    if fault == "literal":
        consumer["query"]["n"] = dict(value=2, from_step=None, select="$")
    elif fault == "selector":
        consumer["query"]["n"]["select"] = "$.wrong.id"
    elif fault == "source_policy":
        args[5][0]["plan"]["steps"][0]["policy_id"] = "unapproved"
    elif fault == "source_role":
        args[5][0]["plan"]["steps"][0]["role"] = "reader"
    result = evaluate(*args)
    assert result["full_execution"] is (fault == "none")


@pytest.mark.asyncio
async def test_fixture_read_only_identity_and_static_state(tmp_path):
    (tmp_path / "active.json").write_text('{"id":"unit"}')
    app = create_fixture(tmp_path, dict(owner="owner-fixture", reviewer="reviewer-fixture"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as c:
        state = (await c.get("/__state")).json()
        assert (
            await c.get("/indexes/41", headers={"Authorization": "Bearer owner-fixture"})
        ).json()["links"][0]["id"] == 73
        assert (await c.get("/indexes/41")).status_code == 401
        assert (
            await c.get("/build", headers={"Authorization": "Bearer reviewer-fixture"})
        ).status_code == 403
        assert (await c.post("/build", json={"build": "changed"})).status_code == 405
        assert (await c.get("/__state")).json() == state


def test_all_twelve_texts_and_answer_shapes_are_preserved():
    from pathlib import Path

    items = tasks(
        Path(__file__).resolve().parents[1] / "docs/mixed-planning-c02-c03-ab-plan-20261107.md"
    )
    assert len(items) == 12 and len({i["request"] for i in items}) == 12
    assert sum(not i["rubric"]["gaps"] for i in items) == 2
    assert len(next(i for i in items if i["id"] == "I04")["rubric"]["gaps"]) == 2


def test_distinct_reads_out_of_order_do_not_satisfy_order():
    args = list(sample(2))
    args[0]["rubric"]["steps"][1]["values"]["query"]["n"] = 3
    args[2][0]["steps"][1]["request_evidence"]["values"]["query"]["n"] = 3
    args[3]["policies"][0]["binding_rules"][0]["literal_schema"]["enum"] = [2, 3]
    args[4][1]["values"]["query"]["n"] = 3
    args[5][0]["plan"]["steps"][1]["query"]["n"]["value"] = 3
    assert evaluate(*args)["full_execution"]
    args[4][0]["sequence"], args[4][1]["sequence"] = 2, 1
    assert not evaluate(*args)["full_execution"]


def test_partial_hold_can_still_be_an_erroneous_global_refusal():
    args = list(sample())
    args[0]["rubric"]["gaps"] = [
        dict(kind="permission", operation="read", role="reviewer", source="reviewer")
    ]
    args[1] = dict(
        focus=dict(
            expectation_questions=[
                dict(
                    operation_id="read",
                    role="reviewer",
                    kind="permission",
                    source_quote="reviewer 权限",
                )
            ]
        ),
        questions=[
            dict(operation_id="read", role="reviewer", kind="permission", status_choices=[403, 404])
        ],
    )
    args[2], args[4], args[5] = [], [], []
    result = evaluate(*args)
    assert result["false_refusal"] and not result["correct_hold"]


def test_extra_approved_read_is_task_scope_error_separate_from_policy_scope():
    args = list(sample(2))
    args[0]["rubric"]["steps"] = args[0]["rubric"]["steps"][:1]
    args[0]["rubric"]["order"] = []
    result = evaluate(*args)
    assert result["configured_scope_violations"] == 0
    assert result["task_scope_violations"] == 1 and result["pending_scope_violations"] == 0


@pytest.mark.parametrize("kind", ["content", "source"])
def test_pending_assertion_or_source_cannot_be_granted_by_the_scorer(kind):
    args = list(sample())
    gap = dict(
        kind=kind,
        operation="read",
        role=None,
        source="$.label",
        selector="$.label",
        operator="equals",
        value="unapproved",
        block_all_roles=True,
    )
    args[0]["rubric"]["steps"] = []
    args[0]["rubric"]["gaps"] = [gap]
    entry = dict(
        selector="$.label",
        operator="equals",
        expected="unapproved",
        source_quote="$.label",
        blocked_operations=["read"],
        status="NEEDS_REVIEW",
    )
    args[1] = {"response_requirements" if kind == "content" else "data_references": [entry]}
    result = evaluate(*args)
    assert not result["correct_hold"]
    assert result["configured_scope_violations"] == 0
    assert result["task_scope_violations"] == result["pending_scope_violations"] == 1


def test_named_fixture_identities_are_declared_in_openapi_security():
    import json

    from scripts.round3_ab.fixture import configs

    for cfg in configs().values():
        operations = {op["operation_id"]: op for op in cfg["generic"]["operations"]}
        for path in json.loads(cfg["document"])["paths"].values():
            op = operations[path["get"]["operationId"]]
            assert path["get"]["security"] == ([{"fixtureBearer": []}] if op["role"] else [])


@pytest.mark.parametrize("fault", ["none", "wrong_body", "wrong_policy", "false_check"])
def test_redacted_field_values_still_need_independent_approval_and_actual_http_body(fault):
    args = list(sample())
    field = dict(select="$.n", operator="equals", expected=2)
    args[0]["rubric"]["steps"][0]["fields"] = [field]
    args[3]["policies"][0]["assertions"]["fields"] = [dict(field)]
    args[4][0]["response"] = {"n": 2}
    args[2][0]["steps"][0]["checks"].append(
        dict(kind="equals", selector="$.n", passed=True, expected="[REDACTED]", actual="[REDACTED]")
    )
    if fault == "wrong_body":
        args[4][0]["response"]["n"] = 3
    elif fault == "wrong_policy":
        args[3]["policies"][0]["assertions"]["fields"][0]["expected"] = 3
    elif fault == "false_check":
        args[2][0]["steps"][0]["checks"][-1]["passed"] = False
    assert evaluate(*args)["full_execution"] is (fault == "none")


def test_an_unnecessary_business_question_cannot_make_a_full_goal_pass():
    args = list(sample())
    args[1] = dict(
        questions=[
            dict(kind="clarification", operation_id="read", question="是否需要已经批准的状态？")
        ]
    )
    result = evaluate(*args)
    assert not result["full_execution"] and result["unnecessary_questions"] == 1


@pytest.mark.asyncio
async def test_external_observer_preserves_inputs_return_values_and_exceptions(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts/round3_ab"))
    from observe import install_observers

    from agenttest.executor import Executor
    from agenttest.product_design import TestDesignModel
    from agenttest.product_focus import TestFocusModel
    from agenttest.product_probe import ProductProbeModel

    def decoded(self, content, context):
        if content == "bad":
            raise ValueError("expected")
        return '{"unchanged":true}'

    async def run(self, scenario, *, report=None):
        return report

    for cls in (TestFocusModel, TestDesignModel, ProductProbeModel):
        monkeypatch.setattr(cls, "prepare_request", lambda self, context: b'{"a":1}')
        monkeypatch.setattr(cls, "_decode_output", decoded)
    monkeypatch.setattr(Executor, "run", run)
    install_observers(tmp_path, ["unit-secret"])
    model = object.__new__(TestFocusModel)
    model.stats = SimpleNamespace(request_sha256="canonical")
    assert model.prepare_request({}) == b'{"a":1}'
    assert model._decode_output('{"private":"unit-secret"}', {}) == '{"unchanged":true}'
    with pytest.raises(ValueError, match="expected"):
        model._decode_output("bad", {})
    report = SimpleNamespace(run_id="unit")
    scenario = SimpleNamespace(model_dump=lambda **kwargs: {"steps": []})
    assert await Executor.run(object.__new__(Executor), scenario, report=report) is report
    records = [json.loads(p.read_bytes()) for p in tmp_path.glob("*.json")]
    assert {r["kind"] for r in records} == {"model_request", "model_response", "execution_plan"}
    assert "unit-secret" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))


def test_operator_adapter_only_lowers_native_time_and_call_capacity(monkeypatch):
    from types import SimpleNamespace

    from agenttest import project_intake
    from agenttest.project_goals import ProjectBudget
    from agenttest.task_store import TaskStore
    from scripts.round3_ab.budget import install

    project = SimpleNamespace(
        registration=SimpleNamespace(
            budget=ProjectBudget(suite_max_model_calls=9, suite_max_total_tokens=500000)
        )
    )
    monkeypatch.setattr(project_intake, "build_generic_project", lambda *a, **kw: project)
    monkeypatch.setattr(TaskStore, "create", lambda *a: a[-1])
    install()
    assert project_intake.build_generic_project().registration.budget.suite_max_model_calls == 4
    summary = dict(mode="MODEL_DESIGNED", budget=dict(max_seconds=600, max_requests=8))
    result = TaskStore.create(object(), "", "", "", "", "", "", "", summary)
    assert result["budget"] == dict(max_seconds=180, max_requests=8)
    assert project.registration.budget.suite_max_total_tokens == 500000
