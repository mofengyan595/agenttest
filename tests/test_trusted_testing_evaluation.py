"""Harness tests use older requests only; no final 12-request position is executed here."""

import importlib
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("langgraph")


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("evaluate_trusted_testing")


def single_label(project):
    negatives = [p for p in project.registry.definitions()
                 if p.negative and p.negative.base_policy_id == "ticket_open_v1"]
    return dict(id="OLD_DEVELOPMENT", scope="single", behavior="execute", eligible=True,
        expected_goals=["create"], scenario_denominator=10, operation_denominator=1,
        expected_groups=[dict(root="ticket_open_v1", count=3,
            steps=[["openTicket", "owner", "ticket_open_v1"]]),
            *[dict(root=p.id, count=1, steps=[["openTicket", "owner", p.id]]) for p in negatives]],
        schema_variants=[dict(policy="ticket_open_v1", field="body.subject", lengths=[1, 80]),
                         dict(policy="ticket_open_v1", field="body.priority",
                              values=["low", "high"])])


@pytest.mark.asyncio
async def test_fixed_development_request_uses_adapter_and_independent_negative_denominator(
    tmp_path, harness,
):
    from agenttest.eval_v2.archive import read_json
    from agenttest.project_goals import load_goal_project

    harness.copied_profiles(tmp_path / "projects")
    folder = tmp_path / "position-001"
    folder.mkdir()
    checks = []
    row = await harness.position(folder, dict(project="tickets", user_task="测试创建工单接口"),
                                 "none", None, lambda: checks.append(True))
    project = load_goal_project(tmp_path / "projects/tickets/registered.project.json")
    product, reports = harness.recount(folder, row, project)
    plans, shapes = [read_json(folder / name) for name in ("plans.json", "shapes.json")]
    result = harness.score(single_label(project), product, reports, plans, shapes, project)
    assert result["full_task_complete"] and checks
    assert result["approved_negative_scenario_denominator"] == 7
    assert result["negative_inputs_executed"] == result["negative_rejected_as_expected"] == 7
    assert row["usage"]["model_calls"] == row["actual_asgi_requests"] == 10
    # A product's own complete flag cannot hide a missing independently required proposal.
    incomplete = deepcopy(plans)
    incomplete.pop(next(iter(incomplete)))
    assert not harness.score(single_label(project), product, reports,
        incomplete, shapes, project)["full_task_complete"]
    changed = deepcopy(row)
    changed["usage"]["model_calls"] += 1
    with pytest.raises(ValueError, match="USAGE_OR_BUDGET"):
        harness.recount(folder, changed, project)
    first = importlib.import_module("trusted_first_demand")
    from agenttest.evaluation import save_evaluation

    save_evaluation(dict(request=dict(max_model_calls=20, max_requests=64,
        max_readonly_investigations=2, max_total_tokens=500000)), folder / "freeze.json")
    save_evaluation(dict(run_id=row["source_report_ids"][0]), folder / "result.json")
    receipt = first.evidence(folder)
    assert receipt["full_task_complete"] and receipt["budget_ok"]
    assert receipt["real_model_calls"] == 1 and not receipt["exported"]
    import json
    import shutil

    from agenttest.eval_v2.archive import inventory

    shutil.copytree(tmp_path / "projects", folder / "projects")
    (folder / "freeze.json").write_bytes(json.dumps(dict(
        request=dict(max_model_calls=20, max_requests=64, max_readonly_investigations=2,
                     max_total_tokens=500000), source=dict(commit="old-development-fixture"),
        profile_inventory=inventory(folder / "projects"))).encode())
    (folder / "result.json").write_bytes(json.dumps(receipt).encode())
    result = first.finalize(folder, recovering=True)
    assert result["archive_verified"] and result["new_model_calls"] == 0
    assert first.verify(folder) == result
    assert (folder / "results.json").exists() and (folder / "summary.md").exists()
    changed_receipt = json.loads((folder / "results.json").read_bytes())
    changed_receipt["result"]["real_model_calls"] += 1
    (folder / "results.json").write_bytes(json.dumps(changed_receipt).encode())
    with pytest.raises(ValueError, match="ARCHIVE_CHANGED"):
        first.verify(folder)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,behavior", [("none", "execute"),
                                           ("evidence_gap", "evidence_insufficient")])
async def test_multi_producer_reference_checks_do_not_come_from_product_steps(
    tmp_path, harness, fault, behavior,
):
    from agenttest.eval_v2.archive import read_json
    from agenttest.project_goals import load_goal_project

    harness.copied_profiles(tmp_path / "projects")
    folder = tmp_path / "position-001"
    folder.mkdir()
    row = await harness.position(folder, dict(project="laboratory", user_task="装载、查验"),
                                 fault, None, lambda: None)
    project = load_goal_project(tmp_path / "projects/laboratory/registered.project.json")
    product, reports = harness.recount(folder, row, project)
    plans, shapes = [read_json(folder / name) for name in ("plans.json", "shapes.json")]
    label = dict(scope="flow", behavior=behavior, eligible=True,
        expected_goals=["place", "lookup"], scenario_denominator=1, operation_denominator=4,
        expected_groups=[dict(root="lab_lookup_v1", count=1, steps=[
            ["makeTray", "tech", "lab_tray_v1"], ["makeSpecimen", "tech", "lab_specimen_v1"],
            ["placeSpecimen", "tech", "lab_place_v1"], ["getPlacement", "tech", "lab_lookup_v1"]])])
    result = harness.score(label, product, reports, plans, shapes, project)
    assert result["plan_complete"] and result["actual_execution_complete"]
    assert result["unauthorized_executions"] == result["wrong_evidence_adopted"] == 0
    if fault == "none":
        assert result["full_task_complete"]
    else:
        assert result["evidence_insufficient_exit"]
        assert result["full_task_complete"]
        assert result["model_explicit_abstentions"] == 1
        assert result["model_correct_abstentions"] == 1
        assert result["rule_supported"] == result["model_supported"] == 0
    changed = deepcopy(plans)
    proof = next(iter(changed.values()))
    consumer = next(s for s in proof["steps"] if s["policy_id"] == "lab_place_v1")
    consumer["selectors"]["path_parameters.specimen_key"] = "$.tray.key"
    assert not harness.score(label, product, reports, changed, shapes, project)["plan_complete"]


@pytest.mark.asyncio
async def test_draft_calls_are_inside_the_same_recount_and_no_labels_are_sent(tmp_path, harness):
    from agenttest.eval_v2.archive import read_json
    from agenttest.project_goals import load_goal_project

    harness.copied_profiles(tmp_path / "projects")
    folder = tmp_path / "position-001"
    folder.mkdir()
    row = await harness.position(folder, dict(project="courses", user_task="创建课程并退款"),
                                 "none", None, lambda: None)
    project = load_goal_project(tmp_path / "projects/courses/registered.project.json")
    product, reports = harness.recount(folder, row, project)
    assert not reports and row["usage"]["model_calls"] == 1 and not row["actual_asgi_requests"]
    assert product["goal_spec"]["source_accounted"]
    assert not product["goal_spec"]["semantic_verified"]
    context = read_json(folder / "semantic-inputs/input-01.json")
    assert set(context) == {"original_request", "project_id", "catalog"}
    assert "expected_groups" not in context


def test_fixed_denominators_survive_unattempted_positions(harness):
    labels = [dict(id="supported", scope="project", behavior="execute", eligible=True),
              dict(id="review", scope="single", behavior="clarify", eligible=False)]
    rows = [dict(case_id="supported", attempted=False), dict(case_id="review", attempted=False)]
    result = harness.summarize(rows, [], labels, "real")
    assert result["full_task_complete"] == dict(numerator=0, denominator=2)
    assert result["eligible_full_task"]["denominator"] == 1
    assert not result["all_quality_targets_met"]


@pytest.mark.asyncio
async def test_dirty_source_stops_before_loading_credentials(tmp_path, harness, monkeypatch):
    monkeypatch.setattr(harness, "checkout_identity", lambda root: dict(worktree_status="dirty"))

    def forbidden(path):
        raise AssertionError("CREDENTIALS_MUST_NOT_BE_READ")

    monkeypatch.setattr(harness, "settings_for", forbidden)
    with pytest.raises(ValueError, match="REQUIRES_CLEAN_COMMIT"):
        await harness.run(tmp_path / "not-created", Path(".env"))
    assert not (tmp_path / "not-created").exists()


@pytest.mark.asyncio
async def test_first_demand_cannot_resume_paid_work_after_a_previous_stop(
    tmp_path, harness, monkeypatch,
):
    first = importlib.import_module("trusted_first_demand")
    monkeypatch.setattr(first, "verify_archive", lambda path: dict(summary=dict(
        source="real", attempted=36, unknown_usage_calls=0, safety={})))
    monkeypatch.setattr(first, "checkout_identity", lambda root: dict(worktree_status=""))
    monkeypatch.setattr(first, "read_json", lambda path: dict(source=dict(worktree_status=""))
        if path.name == "freeze.json" else dict(results=[dict(stop_paid=True)]))

    def forbidden(path):
        raise AssertionError("NO_PAID_RESUMPTION")

    monkeypatch.setattr(first, "settings_for", forbidden)
    with pytest.raises(ValueError, match="PRIOR_USAGE_GATE_FAILED"):
        await first.run(tmp_path / "not-created", Path(".env"), tmp_path)


def test_new_protocol_metadata_and_identity_exclusion_without_execution(harness):
    from agenttest.eval_v2.archive import read_json
    from agenttest.eval_v2.review import task_identity

    requests = read_json(harness.DATA / "requests.json")
    labels = read_json(harness.DATA / "labels.json")
    protocol = read_json(harness.DATA / "protocol.json")
    assert len(requests) == len({r["id"] for r in requests}) == 12
    assert [label["id"] for label in labels if label["eligible"]] == protocol["eligible_task_ids"]
    assert protocol["eligible_positions"] == 30 and protocol["eligible_project_positions"] == 6
    unknown = next(label for label in labels if label["id"] == "TT06")
    text = next(q["user_task"] for q in requests if q["id"] == "TT06")
    span = unknown["unknown_requirement_span"]
    assert text[span["start"]:span["end"]] == "收费"
    identities = read_json(harness.PROJECT_ROOT / "examples/eval-v2/goal-used-task-identities.json")
    # Registry format is verified without running any new task or model proposal.
    assert all(task_identity(q["user_task"]) in str(identities) for q in
               [*requests, read_json(harness.DATA / "first-demand.json")])


def test_v2_metadata_is_new_and_preserves_budgets_without_executing_requests(harness):
    from collections import Counter

    from agenttest.eval_v2.archive import read_json
    from agenttest.eval_v2.review import task_identity

    data = harness.DATA.with_name("evaluation-v2")
    old = read_json(harness.DATA / "requests.json")
    requests = read_json(data / "requests.json")
    labels = read_json(data / "labels.json")
    protocol = read_json(data / "protocol.json")
    assert {q["id"] for q in requests} == {label["id"] for label in labels}
    assert Counter(label["scope"] for label in labels) == dict(single=6, flow=3, project=3)
    assert len({task_identity(q["user_task"]) for q in requests}) == 12
    assert not {task_identity(q["user_task"]) for q in requests}.intersection(
        task_identity(q["user_task"]) for q in old)
    assert [label["id"] for label in labels if label["eligible"]] == protocol["eligible_task_ids"]
    assert protocol["eligible_positions"] == 30 and protocol["eligible_project_positions"] == 6
    previous = read_json(harness.DATA / "protocol.json")
    assert protocol["per_run"] == previous["per_run"]
    assert {k: v for k, v in protocol["per_position"].items()
        if k != "max_business_and_investigation_requests"} == {
        k: v for k, v in previous["per_position"].items()
        if k != "max_business_and_investigation_requests"}
    span = labels[5]["unknown_requirement_span"]
    assert requests[5]["user_task"][span["start"]:span["end"]] == "折扣"
    first = read_json(data / "first-demand.json")
    assert first["first_attempt_only"] and first["development_execution_forbidden"]
    assert first["max_model_calls"] == 4 and first["max_requests"] == 8


def test_reference_amendment_only_fixes_two_impossible_operation_spellings(harness):
    from agenttest.eval_v2.archive import read_json

    old = harness.DATASETS["v2"]
    amended = harness.DATASETS["v2-amended"]
    for name in ("requests.json", "first-demand.json"):
        assert (old / name).read_bytes() == (amended / name).read_bytes()
    labels = read_json(old / "labels.json")
    expected = deepcopy(labels)
    expected[6]["expected_groups"][0]["steps"][3][0] = "readTicket"
    expected[6]["expected_groups"][0]["steps"][4][0] = "readTicket"
    assert read_json(amended / "labels.json") == expected
    requests = read_json(old / "requests.json")
    with pytest.raises(ValueError, match="REFERENCE_NOT_APPROVED"):
        harness.validate_references(requests, labels)
    harness.validate_references(requests, expected)
    before, after = [read_json(path / "protocol.json") for path in (old, amended)]
    assert before["per_run"] == after["per_run"]
    assert before["per_position"] == after["per_position"]
    assert after["reference_amendment"]["original_scores_preserved"]


def test_cold_archive_rejects_work_after_unknown_usage_and_preserves_all_36(tmp_path, harness):
    from agenttest.eval_v2.archive import inventory, seal
    from agenttest.evaluation import save_evaluation
    from agenttest.project_goals import load_goal_project
    from agenttest.report import ModelCallStats

    harness.copied_profiles(tmp_path / "projects")
    requests = [dict(id=f"DEV_{i}", project="courses", user_task="创建课程并退款")
                for i in range(12)]
    labels = [dict(id=q["id"], scope="single" if i < 6 else "flow" if i < 9 else "project",
        behavior="clarify", eligible=False,
        expected_goals=[], expected_groups=[], scenario_denominator=1, operation_denominator=0)
        for i, q in enumerate(requests)]
    schedule = [dict(position=i + 1, round=r, case_id=q["id"])
        for i, (r, q) in enumerate((r, q) for r in range(1, 4) for q in requests)]
    folder = tmp_path / "position-001"
    usage = dict(requests=0, model_calls=1, known_tokens=0, total_tokens=None, usage_known=False)
    product = dict(scope="single", cases=[dict(case_id="pending", status="NEEDS_REVIEW",
        required_plan=[], requested_goals=[])], http_requests=0, complete=False, usage=usage,
        coverage=dict(scenario_denominator=1, operation_denominator=0),
        goal_spec=dict(semantic_verified=False, source_accounted=False, unresolved=[],
            model_call=ModelCallStats(calls=1, total_tokens=None).model_dump(mode="json")))
    save_evaluation(product, folder / "product.json")
    for name in ("plans.json", "shapes.json"):
        save_evaluation({}, folder / name)
    first = dict(**schedule[0], attempted=True, source_report_ids=[], usage=usage,
        actual_asgi_requests=0, known_input_tokens=0, known_output_tokens=0,
        unknown_usage_calls=1, budget_violations=0, stop_paid=True)
    rows = [first, *[dict(**slot, attempted=False,
        reason="NOT_ATTEMPTED_AFTER_USAGE_INFRA_OR_SAFETY_STOP") for slot in schedule[1:]]]
    project = load_goal_project(tmp_path / "projects/courses/registered.project.json")
    graded = harness.score(labels[0], product, {}, {}, {}, project)
    scores = [dict(**schedule[0], **graded)]
    frozen = dict(schema_version="agenttest.trusted-testing.freeze.v1",
        scoring_version=harness.VERSION,
        requests=requests, labels=labels, schedule=schedule,
        profile_inventory=inventory(tmp_path / "projects"))
    save_evaluation(frozen, tmp_path / "freeze.json")
    save_evaluation(dict(source="mock", results=rows,
        summary=harness.summarize(rows, scores, labels, "mock")), tmp_path / "results.json")
    save_evaluation(dict(version=harness.VERSION, scores=scores), tmp_path / "scores.json")
    for row in rows:
        save_evaluation(row, tmp_path / f"row-{row['position']:03}.json")
    (tmp_path / "summary.md").write_text("synthetic unknown usage archive", encoding="utf-8")
    seal(tmp_path)
    result = harness.verify_archive(tmp_path)
    assert result["archive_verified"] and result["summary"]["scheduled"] == 36
    assert result["summary"]["attempted"] == 1 and result["summary"]["total_tokens"] is None
    # Resealing cannot turn a post-stop position into authorized work.
    import json

    result_path = tmp_path / "results.json"
    changed = json.loads(result_path.read_bytes())
    changed["results"][1].update(attempted=True, source_report_ids=[])
    result_path.write_text(json.dumps(changed), encoding="utf-8")
    (tmp_path / "row-002.json").write_text(json.dumps(changed["results"][1]), encoding="utf-8")
    (tmp_path / "archive.json").write_text(json.dumps(dict(
        schema_version="agenttest.eval.archive.v1", files=inventory(tmp_path))), encoding="utf-8")
    with pytest.raises(ValueError, match="REUSED_REPORT_OR_WORK_AFTER_STOP"):
        harness.verify_archive(tmp_path)
