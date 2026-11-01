from fastapi.testclient import TestClient

from agenttest.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_summary() -> None:
    payload = {
        "document": {
            "openapi": "3.0.3",
            "info": {"title": "demo", "version": "1.0"},
            "paths": {
                "/api/items": {"get": {
                    "operationId": "listItems", "summary": "List items",
                    "responses": {"200": {"description": "Items"}},
                }},
                "/api/items/{id}": {
                    "parameters": [{"name": "id", "in": "path", "required": True,
                                    "schema": {"type": "integer"}}],
                    "get": {"operationId": "getItem",
                            "responses": {"200": {"description": "Item"}}},
                },
            },
        }
    }
    response = client.post("/api/spec/summary", json=payload)
    assert response.status_code == 200
    assert [item["operation_id"] for item in response.json()["operations"]] == [
        "listItems",
        "getItem",
    ]


def test_invalid_openapi_version() -> None:
    response = client.post(
        "/api/spec/summary", json={"document": {"openapi": "2.0", "paths": {}}}
    )
    assert response.status_code == 422
def test_identical_documents_reuse_validation_but_mutations_do_not_cross_callers(monkeypatch):
    import json

    import agenttest.contracts as contracts

    raw = json.dumps(dict(openapi="3.0.3", info=dict(title="Cache isolation probe", version="1"),
        paths={"/unique-cache-probe": {"get": dict(operationId="uniqueCacheProbe",
            responses={"200": dict(description="ok")})}}))
    original = contracts.validate
    observed = []

    def validate(document):
        observed.append(1)
        return original(document)

    monkeypatch.setattr(contracts, "validate", validate)
    first = contracts.load_openapi(raw)
    first.operations[0].responses["200"]["description"] = "caller change"
    first.operations.clear()
    second = contracts.load_openapi(raw.encode())
    assert len(second.operations) == 1
    assert second.operations[0].responses["200"]["description"] == "ok"
    assert len(observed) == 1
    changed = raw.replace("uniqueCacheProbe", "changedCacheProbe")
    assert contracts.load_openapi(changed).operations[0].operation_id == "changedCacheProbe"
    assert len(observed) == 2
