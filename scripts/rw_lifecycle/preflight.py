"""Deterministic lifecycle controls over real loopback HTTP; no model and no real service.

The preparation gate runs first: before explicit human approval the authority refuses to
start, so this script can prove the fixture, the approval model and the scorer agree
without ever sending a state-changing request under an unapproved authority. The controls
then exercise the fixture on its own loopback port and assert the exact verdict each case
must produce.
"""

import contextlib
import json
import secrets
import sys
import threading
import time
from pathlib import Path

import authority
import httpx
import lifecycle
import uvicorn
from write_fixture import ALIASES, PORT, create_fixture, sha

TOKENS = {alias: secrets.token_urlsafe(24) for alias in ALIASES}
RESET = secrets.token_urlsafe(24)
BASE = f"http://127.0.0.1:{PORT}"


@contextlib.contextmanager
def served():
    app = create_fixture(RESET, TOKENS)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            try:
                httpx.get(BASE + "/__state", timeout=0.5, trust_env=False)
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("fixture did not start")
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def headers(role=None, key=None):
    result = {"x-reset-token": RESET}
    if role:
        result["Authorization"] = "Bearer " + TOKENS[role]
    if key:
        result["Idempotency-Key"] = key
    return result


def reset(namespace):
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        client.post(BASE + "/__reset", headers={"x-reset-token": RESET})
        response = client.post(
            BASE + "/__namespace", headers={"x-reset-token": RESET}, json={"value": namespace}
        )
        assert response.status_code == 200, response.text
        return client.get(BASE + "/__state").json()


def audit_rows():
    with httpx.Client(timeout=5, trust_env=False) as client:
        return client.get(BASE + "/__audit").json()["rows"]


def faults(mapping):
    with httpx.Client(timeout=5, trust_env=False) as client:
        client.post(BASE + "/__faults", headers={"x-reset-token": RESET}, json=mapping)


def call(client, method, path, role=None, key=None, body=None):
    return client.request(method, BASE + path, headers=headers(role, key), json=body)


def run_case(name, namespace, actions, case, *, expect=None):
    """Perform one prepared case and score it against the fixture's own audit."""
    reset(namespace)
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        statuses = actions(client)
        state = client.get(BASE + "/__state").json()
    rows = audit_rows()
    result = lifecycle.score(dict(case, namespace=namespace), rows)
    details = dict(
        verdict=result["verdict"],
        findings=result["findings"],
        metrics=result["metrics"],
        statuses=statuses,
        state=state,
        requests=len(rows),
    )
    print(
        f"  {name:<42} {result['verdict']:<13} requests={len(rows)} "
        f"statuses={statuses} findings={[row['reason'] for row in result['findings']]}"
    )
    if expect:
        for reason in expect.get("findings", []):
            assert reason in [row["reason"] for row in result["findings"]], (name, reason)
        if "verdict" in expect:
            assert result["verdict"] == expect["verdict"], (name, result["verdict"])
        if "statuses" in expect:
            assert statuses == expect["statuses"], (name, statuses)
    assert result["metrics"]["namespace_violations"] == 0, name
    return dict(case=name, namespace=namespace, **details)


def lifecycle_controls():
    """Two positive lifecycles and six defect classes that must be reported."""
    allowed = [op["operation_id"] for op in authority.WRITE_AUTHORITY["operations"]]
    results = []

    def full(client):
        created = call(client, "POST", "/items", "author", "key-positive-0001", {"name": "first"})
        item = created.json()["id"]
        statuses = [created.status_code]
        statuses.append(call(client, "GET", f"/items/{item}", "author").status_code)
        statuses.append(
            call(client, "PATCH", f"/items/{item}", "author", body={"name": "renamed"}).status_code
        )
        statuses.append(
            call(client, "PUT", f"/items/{item}", "author", body={"name": "replaced"}).status_code
        )
        statuses.append(call(client, "DELETE", f"/items/{item}", "author").status_code)
        statuses.append(call(client, "GET", f"/items/{item}", "author").status_code)
        return statuses

    results.append(
        run_case(
            "positive_full_lifecycle",
            "e2e-positive-0001",
            full,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=0,
                idempotent=False,
                compensation=False,
            ),
            expect=dict(verdict="PASS", statuses=[201, 200, 200, 200, 204, 404]),
        )
    )

    def replay(client):
        first = call(client, "POST", "/items", "author", "key-replay-00001", {"name": "once"})
        second = call(client, "POST", "/items", "author", "key-replay-00001", {"name": "once"})
        item = first.json()["id"]
        third = call(client, "DELETE", f"/items/{item}", "author")
        fourth = call(client, "GET", f"/items/{item}", "author")
        return [first.status_code, second.status_code, third.status_code, fourth.status_code]

    results.append(
        run_case(
            "idempotent_replay",
            "e2e-replay-00001",
            replay,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=0,
                idempotent=True,
                compensation=False,
            ),
            expect=dict(verdict="PASS", statuses=[201, 200, 204, 404]),
        )
    )

    def wrong_identity(client):
        item = call(
            client, "POST", "/items", "author", "key-identity-001", {"name": "owned"}
        ).json()["id"]
        denied = call(client, "PATCH", f"/items/{item}", "editor", body={"name": "hijack"})
        statuses = [denied.status_code]
        statuses.append(call(client, "GET", f"/items/{item}", "auditor").status_code)
        statuses.append(call(client, "DELETE", f"/items/{item}", "author").status_code)
        statuses.append(call(client, "GET", f"/items/{item}", "author").status_code)
        return statuses

    results.append(
        run_case(
            "wrong_identity_and_resource_scope",
            "e2e-identity-0001",
            wrong_identity,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=2,
                idempotent=False,
                compensation=False,
            ),
            expect=dict(verdict="PASS", statuses=[403, 403, 204, 404]),
        )
    )

    def wrong_key(client):
        first = call(client, "POST", "/items", "author", "key-duplicate-01", {"name": "one"})
        second = call(client, "POST", "/items", "author", "key-duplicate-02", {"name": "two"})
        return [first.status_code, second.status_code]

    results.append(
        run_case(
            "duplicate_submission_without_reused_key",
            "e2e-duplicate-001",
            wrong_key,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=False,
                denied=0,
                idempotent=False,
                compensation=False,
            ),
            expect=dict(verdict="FAIL", statuses=[201, 201], findings=["create_count_mismatch"]),
        )
    )

    def leftovers(client):
        created = call(client, "POST", "/items", "author", "key-leftover-0001", {"name": "kept"})
        return [created.status_code]

    results.append(
        run_case(
            "missing_cleanup_leaves_resource",
            "e2e-leftover-0001",
            leftovers,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=0,
                idempotent=False,
                compensation=False,
            ),
            expect=dict(verdict="FAIL", findings=["cleanup_unverified"]),
        )
    )

    def fault_uncompensated(client):
        item = call(
            client, "POST", "/items", "author", "key-fault-000001", {"name": "before"}
        ).json()["id"]
        faults({"updateItem": "after_commit"})
        broken = call(client, "PATCH", f"/items/{item}", "author", body={"name": "after"})
        faults({})
        return [broken.status_code]

    results.append(
        run_case(
            "post_commit_fault_without_compensation",
            "e2e-fault-000001",
            fault_uncompensated,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=0,
                idempotent=False,
                compensation=True,
            ),
            expect=dict(verdict="FAIL", findings=["compensation_missing", "cleanup_unverified"]),
        )
    )

    def fault_compensated(client):
        item = call(
            client, "POST", "/items", "author", "key-fault-000002", {"name": "before"}
        ).json()["id"]
        faults({"updateItem": "after_commit"})
        broken = call(client, "PATCH", f"/items/{item}", "author", body={"name": "after"})
        faults({})
        cleaned = call(client, "DELETE", f"/items/{item}", "author")
        gone = call(client, "GET", f"/items/{item}", "author")
        return [broken.status_code, cleaned.status_code, gone.status_code]

    results.append(
        run_case(
            "post_commit_fault_with_compensation",
            "e2e-fault-000002",
            fault_compensated,
            dict(
                created=1,
                allowed_operations=allowed,
                identity="author",
                cleanup=True,
                denied=0,
                idempotent=False,
                compensation=True,
            ),
            expect=dict(verdict="PASS", statuses=[500, 204, 404]),
        )
    )

    def namespaced(client):
        call(client, "POST", "/items", "author", "key-namespace-01", {"name": "here"})
        return []

    first = run_case(
        "namespace_a_creates_one_item",
        "e2e-namespace-aa",
        namespaced,
        dict(
            created=1,
            allowed_operations=allowed,
            identity="author",
            cleanup=False,
            denied=0,
            idempotent=False,
            compensation=False,
        ),
        expect=dict(verdict="PASS"),
    )
    results.append(first)
    reset("e2e-namespace-bb")
    with httpx.Client(timeout=5, trust_env=False) as client:
        foreign = client.get(BASE + "/items/itm-0001", headers=headers("author"))
    print(
        f"  {'namespace_isolations':<42} {'PASS':<13} "
        f"status={foreign.status_code} (cross-namespace read)"
    )
    assert foreign.status_code == 404, foreign.status_code
    results.append(
        dict(
            case="namespace_isolation",
            verdict="PASS",
            findings=[],
            metrics=dict(cross_namespace_status=foreign.status_code),
            statuses=[404],
            requests=1,
            namespace="e2e-namespace-bb",
            state=dict(state_sha256=sha(b""), items=0),
        )
    )
    assert first["metrics"]["creates"] == 1

    def reset_proof():
        reset("e2e-reset-000001")
        with httpx.Client(timeout=5, trust_env=False) as client:
            empty = client.get(BASE + "/__state").json()
            client.post(
                BASE + "/items",
                headers=headers("author", "key-reset-000001"),
                json={"name": "temp"},
            )
            filled = client.get(BASE + "/__state").json()
            client.post(BASE + "/__reset", headers={"x-reset-token": RESET})
            cleared = client.get(BASE + "/__state").json()
        return empty, filled, cleared

    empty, filled, cleared = reset_proof()
    assert filled["items"] == 1 and cleared["items"] == 0 and cleared["audit"] == 0, cleared
    assert empty["state_sha256"] == cleared["state_sha256"] or cleared["namespace"] == "unset"
    results.append(
        dict(
            case="reset_restores_empty_state",
            verdict="PASS",
            findings=[],
            metrics=dict(
                before=filled["items"], after=cleared["items"], audit_after=cleared["audit"]
            ),
            statuses=[],
            requests=0,
            namespace="e2e-reset-000001",
            state=cleared,
        )
    )
    print(
        f"  {'reset_restores_empty_state':<42} {'PASS':<13} "
        f"items {filled['items']} -> {cleared['items']}, audit {cleared['audit']}"
    )
    return results


def authority_gate():
    """The unapproved declaration must refuse to start; approval flips only that flag."""
    print("authority gate")
    try:
        authority.startable()
    except PermissionError as error:
        print(f"  unapproved declaration refuses to start: {error}")
        blocked = str(error)
    else:
        raise AssertionError("WRITE_AUTHORITY_NOT_GRANTED was not enforced")
    approved = dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED")
    assert authority.startable(approved) is True
    for broken in (
        dict(approved, environment="local"),
        dict(approved, isolated_confirmed=False),
        dict(approved, allow_mutations=False),
        dict(approved, base_url="http://10.0.0.9:80"),
    ):
        try:
            authority.startable(broken)
        except PermissionError:
            continue
        raise AssertionError("authority guard accepted " + json.dumps(broken)[:80])
    print("  every guard (approval, isolation, mutation, loopback) refuses the unsafe case")
    return dict(blocked_reason=blocked, approval_checklist=list(authority.APPROVAL_CHECKLIST))


def main(target):
    report = dict(model_calls=0, target=BASE, isolated=True, loopback_only=True)
    report["authority"] = authority_gate()
    print("lifecycle controls over real loopback HTTP")
    with served():
        report["controls"] = lifecycle_controls()
    verdicts = {row["case"]: row["verdict"] for row in report["controls"]}
    passed = sum(value == "PASS" for value in verdicts.values())
    failed = sum(value == "FAIL" for value in verdicts.values())
    # Every case was already asserted against its expected verdict; the mix proves the
    # scorer accepts correct lifecycles and reports each defect class on the same target.
    report["passed"] = len(report["controls"]) == 10 and (passed, failed) == (7, 3)
    report["passed_controls"], report["failed_controls"] = passed, failed
    assert report["passed"], (passed, failed, verdicts)
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("verdicts:", verdicts)
    print("report:", target)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "runs/rw-lifecycle-20261108/preflight.json")
