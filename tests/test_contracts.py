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
