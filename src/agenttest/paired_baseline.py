"""Developer-authored fixed HTTP assertions; no planner or AgentTest executor on this path."""

import time

import httpx

from agenttest.paired_tasks import paired_inputs


async def fixed_case(case: dict, *, healthy=False) -> dict:
    started = time.perf_counter()
    if case["invalid_kind"]:
        # Same local policy boundary as the agent arm; no unsupported negative HTTP contract.
        _, _, _, _, _, task, _, _ = paired_inputs(case)
        fields = {arg.name: arg.value for step in task.requirements.steps
                  for arg in step.arguments if arg.location == "body"}
        name = fields.get("name", fields.get("subject"))
        rejected = (not isinstance(name, str) or not 1 <= len(name) <= 80 or
                    case["project"] == "tickets" and fields["priority"] not in {"low", "high"})
        return {"outcome": "SAFE_REJECTION" if rejected else "UNEXPECTED_ACCEPT",
                "checks": [], "requests": 0, "elapsed_ms": (time.perf_counter()-started)*1000}
    app, _, _, _, env, task, _, _ = paired_inputs(case, healthy=healthy)
    fields = {arg.name: arg.value for step in task.requirements.steps
              for arg in step.arguments if arg.location == "body"}
    checks, requests = [], 0

    def check(operation, kind, selector, passed):
        checks.append([operation, kind, selector, bool(passed)])
        return passed

    def result():
        return {"outcome": "PASS" if all(c[3] for c in checks) else "ASSERTION_FAILED",
            "checks": checks, "requests": requests,
            "elapsed_ms": (time.perf_counter()-started)*1000}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                base_url="http://127.0.0.1") as client:
        async def request(method, path, **kwargs):
            nonlocal requests
            requests += 1
            return await client.request(method, path, **kwargs)

        if case["project"] == "courses":
            roles = ["alice", "bob"] if case["task"] == "ownership" else ["alice"]
            headers = {}
            for role in roles:
                response = await request("POST", "/api/auth/login", json={
                    "username": env[f"DEMO_{role.upper()}_USERNAME"],
                    "password": env[f"DEMO_{role.upper()}_PASSWORD"]})
                if not check("login", "http_status", None, response.status_code == 200):
                    return result()
                token = response.json().get("access_token")
                if not check("login", "exists", "$.access_token", token is not None):
                    return result()
                headers[role] = {"Authorization": "Bearer " + token}
            response = await request("POST", "/api/courses", headers=headers["alice"],
                                     json={"name": fields["name"]})
            if not check("createCourse", "http_status", None, response.status_code == 201):
                return result()
            key = response.json().get("courseId")
            if not check("createCourse", "exists", "$.courseId", key is not None):
                return result()
            response = await request("GET", f"/api/courses/{key}", headers=headers["alice"])
            if not check("getCourse", "http_status", None, response.status_code == 200):
                return result()
            body = response.json()
            for selector, passed in (("$.courseId", body.get("courseId") == key),
                                     ("$.name", body.get("name") == fields["name"])):
                check("getCourse", "equals", selector, passed)
            if any(not c[3] for c in checks):
                return result()
            if case["task"] == "ownership":
                response = await request("GET", f"/api/courses/{key}", headers=headers["bob"])
                check("getCourse", "http_status", None, response.status_code == 403)
        else:
            headers = {"Authorization": "Bearer " + env["TICKET_OWNER_BEARER"]}
            response = await request("POST", "/v2/tickets", headers=headers,
                json={"subject": fields["subject"], "priority": fields["priority"]})
            if not check("openTicket", "http_status", None, response.status_code == 201):
                return result()
            body = response.json()["payload"]
            key = body.get("ticket_key")
            check("openTicket", "exists", "$.payload.ticket_key", key is not None)
            check("openTicket", "equals", "$.payload.state", body.get("state") == "open")
            operations = ["readTicket"]
            if case["task"] == "close":
                operations = ["closeTicket", "readTicket"]
            for operation in operations:
                response = await request("PATCH" if operation == "closeTicket" else "GET",
                    f"/v2/tickets/{key}", headers=headers,
                    **({"json": {"state": "closed"}} if operation == "closeTicket" else {}))
                if not check(operation, "http_status", None, response.status_code == 200):
                    return result()
                body = response.json()["payload"]
                expected = {"ticket_key": key, "subject": fields["subject"],
                    "priority": fields["priority"],
                    "state": "closed" if case["task"] == "close" else "open"}
                for name, value in expected.items():
                    check(operation, "equals", "$.payload." + name, body.get(name) == value)
                if any(not c[3] for c in checks):
                    return result()
            if case["task"] == "ownership":
                response = await request("GET", f"/v2/tickets/{key}", headers={
                    "Authorization": "Bearer " + env["TICKET_VISITOR_BEARER"]})
                check("readTicket", "http_status", None, response.status_code == 403)
    return result()
