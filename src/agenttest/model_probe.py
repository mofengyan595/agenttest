"""Explicit authenticated metadata probe; no completion tokens or response-body logs."""

import httpx

from agenttest.llm_planner import ModelSettings


async def check_connection(settings: ModelSettings,
                           transport: httpx.AsyncBaseTransport | None = None) -> dict:
    result = {"http_status": None, "configured_model_available": False,
              "generation_calls": 0, "reason": "MODEL_CONNECTION_ERROR"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=settings.timeout,
                                     trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", settings.base_url + "/models", headers={
                    "Authorization": "Bearer " + settings.api_key.get_secret_value()}) as response:
                result["http_status"] = response.status_code
                if response.status_code != 200:
                    result["reason"] = "MODEL_API_ERROR"
                    return result
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    body.extend(chunk)
                    if len(body) > settings.max_response_bytes:
                        result["reason"] = "MODEL_RESPONSE_LIMIT"
                        return result
        from agenttest.plan_json import bounded_json

        data = bounded_json(body.decode(), settings.max_response_bytes)
        result["configured_model_available"] = any(
            item.get("id") == settings.model for item in data["data"])
        result["reason"] = "READY" if result["configured_model_available"] else "MODEL_NOT_FOUND"
    except (httpx.TransportError, ValueError, TypeError, KeyError, RecursionError):
        pass
    return result
