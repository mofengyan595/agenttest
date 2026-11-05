"""Export responses must prove their table contents, rather than only HTTP 200."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.report import Outcome, ReportStore
from agenttest.response_content import csv_table


@pytest.mark.asyncio
@pytest.mark.parametrize("media,content,passed", [
    ("text/csv; charset=UTF-8", '\ufeffcontent,rating\r\n"原文，含逗号",5\r\n', True),
    ("text/csv", 'content,rating\n被替换的文字,5\n', False),
    ("text/csv", 'content,content\n原文，含逗号,5\n', False),
    ("text/csv", 'content,rating\n"未闭合,5\n', False),
    ("text/plain", 'content,rating\n原文，含逗号,5\n', False),
])
async def test_csv_actual_response_checks_values_and_preserves_only_hashes(
        tmp_path, media, content, passed):
    raw = json.dumps(dict(openapi="3.0.3", info=dict(title="Exports", version="1"),
        paths={"/export": {"get": dict(operationId="downloadRecords", responses={
            "200": dict(description="CSV", content={"text/csv": dict(schema=dict(
                type="string", format="binary"))})})}}))
    contract = load_openapi(raw)
    fields = analyze_contract(contract)[0]["response_fields"]
    assert {f["selector"] for f in fields} == {"$.row_count", "$.columns", "$.rows"}
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, operations=[dict(operation_id="downloadRecords", alias="导出",
            statuses=[200], checks=[dict(select="$.row_count", expected=1),
                dict(select="$.columns", expected=["content", "rating"]),
                dict(select="$.rows[0].content", expected="原文，含逗号"),
                dict(select="$.rows[0].rating", expected="5")])]))
    project = build_generic_project("web_csv", raw, contract, setup, environ={})
    scenario = Scenario.model_validate(dict(name="核对导出原文", steps=[dict(id="read",
        operation_id="downloadRecords", policy_id=project.registry.definitions()[0].id)]))
    requests = []

    def target(request):
        requests.append(request.method)
        return httpx.Response(200, content=content.encode("utf-8"),
                              headers={"content-type": media})

    report = await Executor(project.target, contract, ReportStore(tmp_path),
        httpx.MockTransport(target), {}, project.registry).run(scenario)
    assert requests == ["GET"]
    assert report.outcome == (Outcome.PASS if passed else Outcome.ASSERTION_FAILED)
    assert report.steps[0].checks[0].passed
    assert "原文，含逗号" not in report.model_dump_json()
    attempt = report.steps[0].attempts[0]
    assert attempt.response_sha256
    if media.startswith("text/csv"):
        assert attempt.response_projection == ("CSV_TABLE_V1" if content.startswith((
            "\ufeff", "content,rating\n被")) else "CSV_INVALID")
    else:
        assert "response_projection" not in attempt.model_dump()


def test_csv_is_bounded_and_preserves_quoted_newlines_without_executing_cells():
    assert csv_table(b'content\n"first\nsecond"\n') == dict(columns=["content"],
        rows=[dict(content="first\nsecond")], row_count=1)
    assert csv_table(b'content\n=1+1\n')["rows"][0]["content"] == "=1+1"
    for content in (b"", b"\xff", b"a,b\nonly_one\n", b"a\n" + b"cell\n" * 1001):
        assert csv_table(content) is None


def test_unicode_table_columns_use_the_same_literal_selector_grammar():
    from agenttest.selectors import select, validate_selector

    table = csv_table('评论内容,评分\n原文,5\n'.encode())
    assert select(table, "$.rows[0].评论内容") == "原文"
    assert select(table, "$.rows[0].评分") == "5"
    assert select(dict(élève="texte"), "$.élève") == "texte"
    for path in ("$.rows[*].评分", "$.rows[?(@.评分)]", "$.rows[0]['评分']", "$.rows.1评分"):
        with pytest.raises(ValueError):
            validate_selector(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,selector,expected", [
    (["评论内容", "评分"], "$.rows[0].评论内容", None),
    (["评论内容", "评分"], "$.rows[0].评价内容", "CSV_ASSERTION_COLUMN_UNKNOWN"),
    (None, "$.rows[0].评论内容", "CSV_HEADER_EVIDENCE_REQUIRED"),
    (["评论内容", "评论内容"], "$.rows[0].评论内容", "CSV_HEADER_EVIDENCE_REQUIRED"),
])
async def test_confirmed_csv_preflight_holds_unverified_paths_before_model_or_http(
        tmp_path, headers, selector, expected):
    from test_product_tasks import service

    from agenttest.diagnostics import ValidationFailure
    from agenttest.project_intake import IntakeInput

    raw = json.dumps(dict(openapi="3.0.3", info=dict(title="Reports", version="1"),
        paths={"/export": {"get": dict(operationId="exportReports", responses={
            "200": dict(description="CSV", content={"text/csv": dict(schema=dict(
                type="string", format="binary"))})})}}))
    checks = [dict(select=selector, operator="exists")]
    if headers is not None:
        checks.append(dict(select="$.columns", expected=headers))
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, assisted=True, operations=[dict(operation_id="exportReports",
            alias="导出", statuses=[200], checks=checks)]))

    def never(request):
        pytest.fail("CSV intake must not call the model or target")

    svc = service(tmp_path, never)
    try:
        payload = IntakeInput(name="导出", document=raw, generic=setup, confirmed=True)
        if expected is None:
            assert svc.intake.save(payload, "operator")["ready"]
        else:
            with pytest.raises(ValidationFailure, match=expected) as exc:
                svc.intake.save(payload, "operator")
            assert exc.value.diagnostic.fields == ["exportReports", selector]
            assert not svc.intake.records()
    finally:
        await svc.close()
