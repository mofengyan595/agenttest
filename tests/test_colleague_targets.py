"""Fixture correctness only: ASGI/offline evidence, not actual HTTP or model acceptance."""

import importlib.util
import runpy
import secrets
from pathlib import Path

import httpx
import pytest
from test_project_inventory import proposed_setup

from agenttest.contracts import summarize_openapi
from agenttest.generic_intake import build_generic_project
from agenttest.llm_planner import ModelSettings
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design import design_context, design_groups

spec = importlib.util.spec_from_file_location("colleague_targets",
    Path(__file__).resolve().parents[1] / "scripts" / "serve_colleague_targets.py")
targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targets)


def test_acceptance_version_is_claimed_once_without_overwriting_old_evidence(tmp_path):
    runner = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" /
                               "run_colleague_acceptance.py"))
    root = tmp_path / "acceptance-v7"
    runner["claim_version"](root)
    original = (root / "first-attempt.claim").read_bytes()
    with pytest.raises(ValueError, match="ACCEPTANCE_VERSION_ALREADY_RECORDED"):
        runner["claim_version"](root)
    assert (root / "first-attempt.claim").read_bytes() == original


def test_three_documents_are_valid_and_large_inventory_is_complete():
    contracts = {kind: summarize_openapi(targets.document(kind))
                 for kind in ("parcels", "reservations", "directory")}
    assert len(contracts["directory"].operations) == 48
    assert len(contracts["parcels"].operations) == 6
    assert len(contracts["reservations"].operations) == 5


def test_acceptance_reference_preflight_rejects_undeclared_order_without_changing_old_score():
    reference_spec = importlib.util.spec_from_file_location("colleague_reference",
        Path(__file__).resolve().parents[1] / "scripts" / "check_colleague_reference.py")
    reference = importlib.util.module_from_spec(reference_spec)
    reference_spec.loader.exec_module(reference)
    task = dict(project="directory", operation_denominator=48, contract_requirements=[dict(
        operation_id="readSection12", location="query", name="order", valid_values=["asc"])])
    manifest = dict(require_contract_preflight=True, tasks=[task])
    documents = dict(directory=targets.document("directory"))
    with pytest.raises(ValueError, match="REFERENCE_FIELD_NOT_DOCUMENTED"):
        reference.check_reference(manifest, documents)
    task["contract_requirements"][0]["operation_id"] = "readSection7"
    result = reference.check_reference(manifest, documents)
    assert result["model_calls"] == result["target_http_requests"] == 0
    assert result["projects"][0]["checked_fields"] == 1
    task["contract_requirements"][0]["invalid_values"] = ["asc"]
    with pytest.raises(ValueError, match="REFERENCE_EXPECTED_INVALID_VALUE_IS_VALID"):
        reference.check_reference(manifest, documents)


def test_development_parcel_design_context_stays_inside_one_bounded_request():
    import json

    contract = summarize_openapi(targets.document("parcels"))
    setup = proposed_setup(contract)
    setup.allow_mutations = True
    setup.approve_negative_tests = True
    project = build_generic_project("parcels", json.dumps(targets.document("parcels")),
                                    contract, setup, environ={})
    model = DesignModel(ModelSettings(model="offline", base_url="https://unit.invalid/v1",
        api_key="offline-only-private-key", max_input_tokens=60_000, max_output_tokens=8192,
        max_total_tokens=70_000), profile="generic")
    group = design_groups(project)[0]
    context = design_context(project, "新建后读取、修改并删除测试包裹", group, 40, 6)
    model.prepare_request(context)
    assert model.stats.input_token_upper_bound <= 60_000


@pytest.mark.asyncio
async def test_directory_evidence_keeps_only_disposable_query_fields(tmp_path):
    import json

    events = tmp_path / "events.jsonl"
    target = targets.create_target("directory", events=events)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target),
                                base_url="http://127.0.0.1:18881") as client:
        result = await client.get("/sections/7", params=dict(limit="exact_probe", order="asc",
            token="must-not-log"), headers={"Authorization": "Bearer must-not-log"})
    assert result.status_code == 422
    assert json.loads(events.read_bytes()) == dict(method="GET", path="/sections/7",
                                                  query=dict(limit="exact_probe", order="asc"))
    assert b"must-not-log" not in events.read_bytes()


@pytest.mark.asyncio
async def test_nested_crud_and_invalid_input_fixture_are_consistent():
    target = targets.create_target("parcels")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target),
                                base_url="http://127.0.0.1:18881") as client:
        body = dict(name="fixture", quantity=2, meta=dict(labels=["blue", "green"],
                                                         options=dict(tracked=True)))
        created = await client.post("/parcels", json=body)
        assert created.status_code == 201
        key = created.json()["id"]
        assert (await client.get("/parcels/" + key)).json()["meta"] == body["meta"]
        changed = await client.patch("/parcels/" + key, json=dict(name="changed"))
        assert changed.status_code == 200 and changed.json()["name"] == "changed"
        assert (await client.post("/parcels", json=dict(body, quantity=0))).status_code == 422
        assert (await client.delete("/parcels/" + key)).status_code == 204
        assert (await client.get("/parcels/" + key)).status_code == 404
        assert not target.state.records


@pytest.mark.asyncio
async def test_login_and_owner_boundary_fixture_use_separate_credentials():
    env = {}
    for alias in ("OWNER", "READER"):
        env[f"AGENTTEST_TARGET_LAB_{alias}_USERNAME"] = alias.lower()
        for field in ("PASSWORD", "BEARER"):
            env[f"AGENTTEST_TARGET_LAB_{alias}_{field}"] = secrets.token_urlsafe(24)
    target = targets.create_target("reservations", env)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target),
                                base_url="http://127.0.0.1:18882") as client:
        tokens = {}
        for alias in ("OWNER", "READER"):
            login = await client.post("/session", json=dict(username=alias.lower(),
                password=env[f"AGENTTEST_TARGET_LAB_{alias}_PASSWORD"]))
            assert login.status_code == 200
            tokens[alias] = login.json()["access_token"]
        body = dict(title="fixture", slot=dict(day="Monday", seats=[1, 2]))
        created = await client.post("/reservations", json=body,
                                   headers=dict(authorization="Bearer " + tokens["OWNER"]))
        assert created.status_code == 201
        path = "/reservations/" + created.json()["reservation_id"]
        assert (await client.get(path, headers=dict(
            authorization="Bearer " + tokens["READER"]))).status_code == 403
        assert (await client.get(path, headers=dict(
            authorization="Bearer " + tokens["OWNER"]))).status_code == 200
        assert (await client.delete(path, headers=dict(
            authorization="Bearer " + tokens["OWNER"]))).status_code == 204


@pytest.mark.asyncio
async def test_large_project_query_parameters_and_bounds():
    target = targets.create_target("directory")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target),
                                base_url="http://127.0.0.1:18883") as client:
        valid = await client.get("/sections/1", params=dict(limit=3, active=False))
        assert valid.status_code == 200 and len(valid.json()["items"]) == 3
        assert valid.json()["items"][0]["active"] is False
        assert (await client.get("/sections/1", params=dict(limit=4))).status_code == 422
        invalid = await client.get("/sections/1", params=dict(active="wrong-type"))
        assert invalid.status_code == 422
        assert (await client.get("/sections/31", params=dict(order="desc"))).status_code == 200
        assert (await client.get("/sections/31", params=dict(order="outside"))).status_code == 422
