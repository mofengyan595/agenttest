"""Read-only pre-run consistency gate for the frozen v4 positions.

A freeze record is only trustworthy if the code, model identity, target, budget and wording that
are about to run can be compared with what was frozen. This module never sends a request and never
writes a frozen artifact: it computes the current view, reports every difference with a closed
reason code, and records an exclusive (never overwritten) execution manifest.

Substantive drift in a frozen artifact always blocks. A code upgrade may proceed only when the
executing commit is named explicitly; the difference is then recorded file by file in the
manifest, so a later reader can tell an audited upgrade from a silent edit.
"""

import hashlib
import json
import subprocess
import time
from pathlib import Path

IDENTITY_FIELDS = ("model", "protocol", "base_url", "timeout", "max_output_tokens",
                   "max_input_tokens")
BODYLESS = {204, 304}


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    # Diffs may contain non-ASCII source text; decode as UTF-8 instead of the console locale.
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def source_snapshot(root):
    """The same source identity ``freeze`` records, computed without writing anything."""
    root = Path(root)
    files = sorted((str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
                   for path in (root / "src/agenttest").rglob("*.py"))
    return dict(head=git(root, "rev-parse", "HEAD"), status=git(root, "status", "--porcelain"),
                diff_sha256=digest(git(root, "diff")), src_sha256=digest(files))


def requirement_view(plan, cases, consumed):
    """The wording, rubric and position record a verdict depends on."""
    return dict(cases=cases, digest=digest(cases), consumed_positions=list(consumed),
                plan=plan.__name__,
                plan_sha256=hashlib.sha256(Path(plan.__file__).read_bytes()).hexdigest(),
                rubric=plan.RUBRIC, dev_cases=[case["key"] for case in plan.DEV_CASES],
                final_cases=[case["key"] for case in plan.FINAL_CASES])


def authority_view(approved, checklist):
    return dict(declaration=approved, digest=digest(approved), checklist=list(checklist))


def contract_view(document, setup):
    return dict(document=json.loads(document), document_sha256=digest(document),
                setup_sha256=digest(setup))


def normalize_identity(identity):
    from urllib.parse import urlsplit

    normalized = {field: (identity or {}).get(field) for field in IDENTITY_FIELDS}
    base_url = normalized.get("base_url") or ""
    parts = urlsplit(base_url)
    normalized["base_url"] = (f"{parts.scheme.lower()}://{parts.netloc.lower()}"
                              f"{parts.path.rstrip('/')}" if parts.scheme and parts.netloc
                              else base_url.rstrip("/"))
    normalized["protocol"] = str(normalized.get("protocol") or "").lower()
    for field in ("timeout", "max_output_tokens", "max_input_tokens"):
        if normalized.get(field) is not None:
            normalized[field] = float(normalized[field])
    return normalized


def identity_drift(frozen_identity, current_identity):
    frozen = normalize_identity(frozen_identity)
    current = normalize_identity(current_identity)
    return [field for field in IDENTITY_FIELDS if frozen.get(field) != current.get(field)]


def snapshot(root, *, plan, cases, consumed, approved, checklist, document, setup, identity,
             initial, base_url):
    """The freeze-shaped view of the code that is about to run."""
    return dict(
        frozen_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        source=source_snapshot(root),
        requirements=requirement_view(plan, cases, consumed),
        authority=authority_view(approved, checklist),
        contract=contract_view(document, setup),
        scorer=dict(sha256=hashlib.sha256(
            (Path(root) / "scripts/rw_lifecycle/lifecycle.py").read_bytes()).hexdigest()),
        model=identity,
        target=dict(base_url=base_url, initial=initial or {}, isolated=True, loopback_only=True),
        budgets=approved["budgets"],
    )


def changed_files(root, frozen_head, executing_head, limit=200):
    """Files changed between the frozen and the executing commit; bounded and never guessing."""
    if not frozen_head or not executing_head or frozen_head == executing_head:
        return []
    try:
        names = git(root, "diff", "--name-status", frozen_head, executing_head).splitlines()
    except subprocess.CalledProcessError:
        return ["<unresolved>"]
    return names[:limit]


def _missing(frozen, path):
    node = frozen
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return True
        node = node[key]
    return False


def compare(frozen, current):
    """Every difference between the frozen record and the executing view.

    ``blocking`` is closed and ordered: substantive artifacts first, then identity/target.
    ``code`` is audit-only: a different commit is allowed when explicitly named at run time.
    """
    blocking, warnings = [], []
    for path in (("requirements", "cases"), ("requirements", "rubric"),
                 ("requirements", "plan_sha256"), ("authority", "declaration"),
                 ("contract", "document_sha256"), ("contract", "setup_sha256"),
                 ("scorer", "sha256"), ("model", "model"), ("target", "base_url"),
                 ("budgets", "max_requests")):
        if _missing(frozen, path):
            blocking.append(dict(reason="FROZEN_DECLARATION_MISSING",
                                 detail=".".join(path)))
    if blocking:
        return dict(allowed=False, blocking=blocking, warnings=warnings, code={}, identity={},
                    target={}, positions={})

    if frozen["requirements"].get("plan") != current["requirements"]["plan"]:
        blocking.append(dict(reason="PLAN_RECORD_CHANGED",
                             detail=[frozen["requirements"].get("plan"),
                                     current["requirements"]["plan"]]))
    if frozen["requirements"]["plan_sha256"] != current["requirements"]["plan_sha256"]:
        blocking.append(dict(reason="PLAN_FILE_CHANGED",
                             detail=[frozen["requirements"]["plan_sha256"][:12],
                                     current["requirements"]["plan_sha256"][:12]]))
    if frozen["requirements"]["digest"] != current["requirements"]["digest"]:
        frozen_cases = {case["key"]: case for case in frozen["requirements"]["cases"]}
        current_cases = {case["key"]: case for case in current["requirements"]["cases"]}
        keys = sorted(set(frozen_cases) | set(current_cases))
        changed = [key for key in keys
                   if digest(frozen_cases.get(key)) != digest(current_cases.get(key))]
        blocking.append(dict(reason="CASES_CHANGED", detail=changed or keys))
    if (digest(frozen["requirements"].get("rubric"))
            != digest(current["requirements"]["rubric"])):
        blocking.append(dict(reason="RUBRIC_CHANGED"))
    if frozen["requirements"].get("consumed_positions") != current["requirements"][
            "consumed_positions"]:
        blocking.append(dict(reason="CONSUMED_POSITIONS_CHANGED",
                             detail=[frozen["requirements"].get("consumed_positions"),
                                     current["requirements"]["consumed_positions"]]))
    if (frozen["requirements"].get("final_cases") != current["requirements"]["final_cases"]
            or frozen["requirements"].get("dev_cases") != current["requirements"]["dev_cases"]):
        blocking.append(dict(reason="POSITION_LIST_CHANGED"))
    if frozen["authority"].get("digest") != current["authority"]["digest"]:
        blocking.append(dict(reason="AUTHORITY_CHANGED"))
    if (digest(frozen["authority"].get("declaration"))
            != digest(current["authority"]["declaration"])):
        blocking.append(dict(reason="AUTHORITY_DECLARATION_CHANGED"))
    if frozen["contract"].get("document_sha256") != current["contract"]["document_sha256"]:
        blocking.append(dict(reason="CONTRACT_DOCUMENT_CHANGED"))
    if frozen["contract"].get("setup_sha256") != current["contract"]["setup_sha256"]:
        blocking.append(dict(reason="CONTRACT_SETUP_CHANGED"))
    if frozen.get("scorer", {}).get("sha256") != current["scorer"]["sha256"]:
        blocking.append(dict(reason="SCORER_CHANGED"))
    if frozen.get("budgets") != current["budgets"]:
        blocking.append(dict(reason="BUDGETS_CHANGED",
                             detail=[frozen.get("budgets"), current["budgets"]]))
    drift = identity_drift(frozen.get("model"), current["model"])
    if drift:
        blocking.append(dict(reason="MODEL_IDENTITY_DRIFT", detail=drift))

    target = dict(
        frozen_base_url=frozen["target"].get("base_url"),
        current_base_url=current["target"].get("base_url"),
        frozen_isolated=frozen["target"].get("isolated"),
        current_isolated=current["target"].get("isolated"),
        frozen_initial=(frozen["target"].get("initial") or {}).get("state_sha256"),
        current_initial=(current["target"].get("initial") or {}).get("state_sha256"),
    )
    if target["frozen_base_url"] != target["current_base_url"]:
        blocking.append(dict(reason="TARGET_CHANGED", detail=[target["frozen_base_url"],
                                                              target["current_base_url"]]))
    if not target["current_isolated"] or not current["target"].get("loopback_only"):
        blocking.append(dict(reason="TARGET_NOT_ISOLATED"))
    if (target["frozen_initial"] and target["current_initial"]
            and target["frozen_initial"] != target["current_initial"]):
        blocking.append(dict(reason="INITIAL_STATE_CHANGED",
                             detail=[target["frozen_initial"][:12],
                                     target["current_initial"][:12]]))

    source = dict(
        frozen_head=frozen["source"].get("head"),
        executing_head=current["source"].get("head"),
        frozen_src_sha256=frozen["source"].get("src_sha256"),
        executing_src_sha256=current["source"].get("src_sha256"),
        frozen_status=frozen["source"].get("status"),
        executing_status=current["source"].get("status"),
        changed=bool(frozen["source"].get("head") != current["source"].get("head")
                     or frozen["source"].get("src_sha256") != current["source"].get("src_sha256")),
    )
    if source["executing_status"]:
        warnings.append(dict(reason="EXECUTING_TREE_DIRTY",
                             detail=source["executing_status"].splitlines()[:8]))
    if source["changed"]:
        warnings.append(dict(reason="SOURCE_CHANGED", detail=source["executing_head"]))
    return dict(allowed=not blocking, blocking=blocking, warnings=warnings,
                code=source, identity=dict(frozen=normalize_identity(frozen.get("model")),
                                           current=normalize_identity(current["model"]),
                                           drift=drift),
                target=target, positions=current["requirements"],
                artifacts=dict(
                    cases=dict(frozen=frozen["requirements"]["digest"],
                               executing=current["requirements"]["digest"]),
                    plan=dict(frozen=frozen["requirements"]["plan_sha256"],
                              executing=current["requirements"]["plan_sha256"]),
                    rubric=dict(frozen=digest(frozen["requirements"].get("rubric")),
                                executing=digest(current["requirements"]["rubric"])),
                    authority=dict(frozen=frozen["authority"].get("digest"),
                                   executing=current["authority"]["digest"]),
                    contract=dict(frozen=frozen["contract"].get("document_sha256"),
                                  executing=current["contract"]["document_sha256"]),
                    scorer=dict(frozen=frozen.get("scorer", {}).get("sha256"),
                                executing=current["scorer"]["sha256"]),
                ))


def manifest_payload(report, frozen, *, accept_code_change=None, initial=None, root=None):
    """The exclusive record of one intended execution."""
    source = dict(report.get("code") or {})
    if root and source.get("frozen_head") and source.get("executing_head"):
        source["changed_files"] = changed_files(root, source["frozen_head"],
                                                source["executing_head"])
        counts = {"added": 0, "modified": 0, "deleted": 0, "renamed": 0, "other": 0}
        for line in source["changed_files"]:
            code = line.split("\t", 1)[0][:1]
            key = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(
                code, "other")
            counts[key] += 1
        source["change_summary"] = counts
    return dict(
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        allowed=report["allowed"],
        blocking=report["blocking"],
        warnings=report["warnings"],
        frozen_head=frozen.get("source", {}).get("head"),
        executing_head=source.get("executing_head"),
        accept_code_change=accept_code_change,
        source=source,
        artifact_digests=dict(
            cases=(report.get("artifacts") or {}).get("cases"),
            plan=(report.get("artifacts") or {}).get("plan"),
            rubric=(report.get("artifacts") or {}).get("rubric"),
            authority=(report.get("artifacts") or {}).get("authority"),
            contract=(report.get("artifacts") or {}).get("contract"),
            scorer=(report.get("artifacts") or {}).get("scorer"),
        ),
        model=report.get("identity"),
        target=report.get("target"),
        acceptance_conditions=dict(
            rubric_check=frozen.get("rubric_check"),
            budgets=frozen.get("budgets"),
            positions=frozen["requirements"].get("final_cases"),
            consumed_positions=frozen["requirements"].get("consumed_positions"),
            cleanup_rule=frozen["authority"]["declaration"].get("cleanup_rule"),
            namespace_rule=frozen["authority"]["declaration"].get("namespace_rule"),
            idempotency_rule=frozen["authority"]["declaration"].get("idempotency_rule"),
            initial_state=(initial or (frozen.get("target", {}).get("initial") or {}))
            .get("state_sha256"),
        ),
    )


def write_manifest(folder, payload):
    """Create a new manifest file; an existing one is never overwritten."""
    folder = Path(folder)
    short = (payload.get("executing_head") or "unknown")[:7]
    for index in range(1, 1000):
        name = f"execution-manifest-{short}" + ("" if index == 1 else f"-{index}") + ".json"
        path = folder / name
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            continue
        return path
    raise SystemExit("MANIFEST_NAMESPACE_EXHAUSTED")


def case_checklist(frozen, report, *, cases, consumed, result, manifest, identity_offline):
    """The nine pre-flight items per frozen position, each with its own evidence."""
    blocking = {item["reason"] for item in report["blocking"]}
    code = report.get("code") or {}
    result_requests = {}
    for row in (result or {}).get("results", []):
        result_requests[row.get("case")] = row.get("requests")
    items = {}
    for case in cases:
        key = case["key"]
        rows = []
        rows.append(dict(
            item="position_unconsumed",
            verdict="BLOCKED" if key in consumed else "READY",
            evidence=dict(consumed=list(consumed), key=key)))
        rows.append(dict(
            item="code_diff_auditable",
            verdict="READY" if manifest and not blocking else "BLOCKED",
            evidence=dict(manifest=Path(manifest).name if manifest else None,
                          frozen_head=code.get("frozen_head"),
                          executing_head=code.get("executing_head"),
                          changed=code.get("changed"))))
        drift = report["identity"].get("drift") or []
        rows.append(dict(
            item="model_identity_matches",
            verdict="BLOCKED" if "MODEL_IDENTITY_DRIFT" in blocking else (
                "NEEDS_REVIEW" if identity_offline else "READY"),
            evidence=dict(drift=drift, offline=identity_offline,
                          model=report["identity"].get("current", {}).get("model"))))
        rows.append(dict(
            item="target_is_approved_isolated",
            verdict="BLOCKED" if {"TARGET_CHANGED", "TARGET_NOT_ISOLATED"} & blocking
            else "READY",
            evidence=dict(base_url=report["target"].get("current_base_url"),
                          loopback=True, isolated=report["target"].get("current_isolated"))))
        operations = (frozen["authority"]["declaration"].get("operations") or [])
        rows.append(dict(
            item="writes_within_scope",
            verdict="BLOCKED" if "AUTHORITY_CHANGED" in blocking else "READY",
            evidence=dict(operations=[row["operation_id"] for row in operations],
                          allow_mutations=frozen["authority"]["declaration"]
                          .get("allow_mutations"))))
        budgets = frozen.get("budgets") or {}
        rows.append(dict(
            item="budgets_match_freeze",
            verdict="BLOCKED" if {"BUDGETS_CHANGED", "MODEL_TIMEOUT_CHANGED"} & blocking
            else "READY",
            evidence=dict(budgets=budgets, model_timeout=frozen["authority"]["declaration"]
                          .get("model_timeout"))))
        rows.append(dict(
            item="initial_state_matches",
            verdict="BLOCKED" if "INITIAL_STATE_CHANGED" in blocking else "READY",
            evidence=dict(frozen=(frozen["target"].get("initial") or {}).get("state_sha256"),
                          current=report["target"].get("current_initial"))))
        rows.append(dict(
            item="no_replay_of_unknown_writes",
            verdict="BLOCKED" if result_requests.get(key) else "READY",
            evidence=dict(previous_requests=result_requests.get(key),
                          guard="acceptance.run refuses when result.json already has requests")))
        rows.append(dict(
            item="evidence_completeness",
            verdict="READY" if all(frozen.get(field) for field in
                                   ("requirements", "authority", "contract", "model", "target",
                                    "budgets", "scorer")) else "BLOCKED",
            evidence=dict(fields=sorted(frozen), records=["focus", "findings", "audit", "state",
                                                          "product"])))
        verdict = ("BLOCKED" if any(row["verdict"] == "BLOCKED" for row in rows)
                   else "NEEDS_REVIEW" if any(row["verdict"] == "NEEDS_REVIEW" for row in rows)
                   else "READY")
        items[key] = dict(verdict=verdict, items=rows)
    overall = ("BLOCKED" if any(item["verdict"] == "BLOCKED" for item in items.values())
               else "NEEDS_REVIEW" if any(item["verdict"] == "NEEDS_REVIEW"
                                          for item in items.values()) else "READY")
    return dict(verdict=overall, cases=items)
