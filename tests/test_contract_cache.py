"""Repeated validation may be reused, but bytes, parsing mode and callers stay isolated."""

import json

import pytest

from agenttest.contracts import _validated_openapi, load_openapi


def document(title="cache-test"):
    return dict(openapi="3.0.3", info=dict(title=title, version="1"), paths={
        "/state": {"get": dict(operationId="readState", responses={
            "200": dict(description="ok")})}})


def test_cached_summary_is_detached_and_changed_bytes_are_revalidated():
    raw = json.dumps(document()).encode()
    first = load_openapi(raw)
    first.operations[0].responses["200"]["description"] = "changed by caller"
    first.operations.clear()
    previous_hits = _validated_openapi.cache_info().hits
    second = load_openapi(raw.decode())
    assert _validated_openapi.cache_info().hits == previous_hits + 1
    assert second.operations[0].responses["200"]["description"] == "ok"
    changed = document()
    changed["paths"]["/state"]["get"]["operationId"] = "changedState"
    assert load_openapi(json.dumps(changed)).operations[0].operation_id == "changedState"
    changed["paths"]["/state"]["get"]["parameters"] = "invalid"
    with pytest.raises(ValueError):
        load_openapi(json.dumps(changed))


def test_wrapper_mode_and_failed_validation_do_not_borrow_a_cached_success():
    wrapped = json.dumps(dict(document=document("wrapped-cache-test")))
    assert load_openapi(wrapped, unwrap=True).version == "3.0.3"
    for _ in range(2):
        with pytest.raises(ValueError):
            load_openapi(wrapped)
    assert _validated_openapi.cache_info().maxsize == 16
