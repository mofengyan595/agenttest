"""Atomic, per-position consumption records for the frozen acceptance cases.

A position is one frozen case (``M2``, ``F1``) that may be executed exactly once. The token is
a directory: ``<run folder>/positions/<case>/``. ``mkdir`` is atomic on every filesystem the
project supports, so two processes can never both create it, and a writer that crashes after
the directory exists still leaves the position consumed. Files inside are supplementary
evidence; none of them can make a consumed position runnable again.

State is derived from the files, never stored as a mutable field:

* ``UNCONSUMED``                  no directory and no historical execution evidence
* ``HISTORICAL_CONSUMED``         listed in the frozen plan's consumed positions (for example M1)
* ``LEGACY_CONSUMED``             an older ``result.json`` shows the case was executed
* ``CLAIMED``                     the directory exists, execution had not started
* ``RUNNING``                     ``running.json`` exists; the process may still be alive or dead
* ``COMPLETED``                   ``completed.json`` exists, the attempt has a verdict
* ``INTERRUPTED_OR_UNKNOWN``      an abnormal end or an unreadable/incomplete record

Only ``UNCONSUMED`` is runnable. Every other state refuses a new attempt: the project must
never replay a write whose outcome is unknown, and a zero-request Focus failure still counts
as the one attempt for that position.
"""

import json
import os
import time
import uuid
from pathlib import Path

SCHEMA = 1
POSITIONS = "positions"


class PositionConsumed(Exception):
    """Raised when a position cannot be claimed; the message names the exact state."""

    def __init__(self, key, status, detail=None):
        self.key = key
        self.status = status
        self.detail = detail
        super().__init__(f"POSITION_ALREADY_CONSUMED_NO_REPLAY:{key}:{status}")


class PositionWriteFailed(Exception):
    """The attempt happened but its final evidence could not be stored."""

    def __init__(self, key, reason):
        self.key = key
        self.reason = reason
        super().__init__(f"POSITION_EVIDENCE_WRITE_FAILED:{key}:{reason}")


def _write_exclusive(path, payload):
    """Write one JSON document that can never replace an existing record."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_text_exclusive(path, text)


def _write_text_exclusive(path, text):
    # newline="\n" keeps the bytes on Windows identical to the text whose digest we record.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path):
    """Persist the directory entry itself; Windows cannot open a directory for fsync."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def folder_for(root, key):
    return Path(root) / POSITIONS / key


def legacy_evidence(root):
    """Summarise an older ``result.json`` without trusting it to be complete.

    Returns ``{"missing": True}``, ``{"corrupt": True}`` or the per-case signals. A corrupt
    historical file is treated as evidence for every planned case: a record that cannot be
    read might describe a request that was sent.
    """
    path = Path(root) / "result.json"
    if not path.is_file():
        return dict(missing=True)
    try:
        done = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return dict(corrupt=True, detail=str(error)[:200])
    if not isinstance(done, dict):
        return dict(corrupt=True, detail=type(done).__name__)
    signals = {}
    requests = done.get("requests") if isinstance(done.get("requests"), dict) else {}
    verdicts = done.get("verdicts") if isinstance(done.get("verdicts"), dict) else {}
    calls = done.get("model_calls") if isinstance(done.get("model_calls"), dict) else {}
    for key in set(requests) | set(verdicts) | set(calls):
        signals.setdefault(key, []).append("summary")
    rows = done.get("results") if isinstance(done.get("results"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row.get("case")
        if not isinstance(key, str) or key == "STOPPED":
            continue
        executed = (row.get("verdict") is not None
                    or row.get("requests") is not None
                    or row.get("metrics", {}).get("requests") is not None
                    or row.get("product", {}).get("requests") is not None
                    or row.get("audit") is not None
                    or row.get("checks") is not None)
        if executed:
            signals.setdefault(key, []).append("result-row")
    return dict(signals=signals, requests=requests, verdicts=verdicts, model_calls=calls)


def legacy_consumed(root, key):
    """A position with historical execution evidence can never be replayed."""
    history = legacy_evidence(root)
    if history.get("corrupt"):
        return True
    return key in (history.get("signals") or {})


def _evidence_integrity(folder, key, completed):
    """Every reason a completion marker cannot be trusted; empty means verified.

    A marker is only ``COMPLETED`` when it names the same case and verdict as a readable
    evidence file whose bytes match the recorded SHA-256. A marker without the digest (an
    older format) is *not* silently trusted: it becomes ``INTERRUPTED_OR_UNKNOWN``.
    """
    problems = []
    if not isinstance(completed, dict):
        return ["completed-not-object"]
    if completed.get("schema") != SCHEMA:
        problems.append("completed-schema")
    if completed.get("case") != key:
        problems.append("completed-case")
    verdict = completed.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        problems.append("completed-verdict")
    if completed.get("evidence") != "evidence.json":
        problems.append("completed-evidence-ref")
    recorded = completed.get("evidence_sha256")
    if not isinstance(recorded, str) or len(recorded) != 64:
        problems.append("evidence-digest-missing")
    path = Path(folder) / "evidence.json"
    if not path.is_file():
        problems.append("evidence-missing")
        return problems
    raw = path.read_bytes()
    if isinstance(recorded, str) and len(recorded) == 64:
        import hashlib

        if hashlib.sha256(raw).hexdigest() != recorded:
            problems.append("evidence-digest-mismatch")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        problems.append("evidence-invalid-json")
        return problems
    if not isinstance(payload, dict):
        problems.append("evidence-not-object")
        return problems
    if payload.get("case") != key:
        problems.append("evidence-case")
    if payload.get("verdict") != verdict:
        problems.append("evidence-verdict")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    if completed.get("requests") != metrics.get("requests"):
        problems.append("evidence-requests")
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    if completed.get("model_calls") != product.get("model_calls"):
        problems.append("evidence-model-calls")
    unknown = "UNKNOWN" in json.dumps(product, ensure_ascii=False)
    if bool(completed.get("unknown")) != unknown:
        problems.append("evidence-unknown")
    return problems


def status(root, key, *, consumed_positions=()):
    """Derive the one-way state of a position from its files and the frozen plan."""
    if key in tuple(consumed_positions):
        return dict(status="HISTORICAL_CONSUMED", signals=["frozen-plan"])
    folder = folder_for(root, key)
    files = {}
    if folder.is_dir():
        for name in ("claim.json", "running.json", "evidence.json", "completed.json",
                     "interrupted.json", "write_failure.json"):
            path = folder / name
            files[name] = path.is_file()
    signals = [name for name, present in files.items() if present]
    if legacy_consumed(root, key):
        signals.append("legacy-result")
    if files.get("completed.json"):
        completed = _read(folder / "completed.json")
        problems = _evidence_integrity(folder, key, completed)
        if problems:
            return dict(status="INTERRUPTED_OR_UNKNOWN", signals=signals + ["integrity"],
                        claim=_read(folder / "claim.json"), completed=completed,
                        integrity=problems)
        return dict(status="COMPLETED", signals=signals,
                    claim=_read(folder / "claim.json"), completed=completed, integrity=[])
    if folder.is_dir() and (files.get("interrupted.json") or files.get("write_failure.json")):
        return dict(status="INTERRUPTED_OR_UNKNOWN", signals=signals,
                    claim=_read(folder / "claim.json"),
                    interrupted=_read(folder / "interrupted.json") or _read(
                        folder / "write_failure.json"))
    if files.get("running.json"):
        return dict(status="RUNNING", signals=signals, claim=_read(folder / "claim.json"))
    if folder.is_dir():
        return dict(status="CLAIMED", signals=signals, claim=_read(folder / "claim.json"))
    if signals:
        return dict(status="LEGACY_CONSUMED", signals=signals)
    return dict(status="UNCONSUMED", signals=[])


def claim(root, key, *, frozen_digest, executing_head, manifest, budgets, identity_digest,
          initial=None, attempt_id=None, frozen_head=None):
    """Atomically take the single attempt for a position; any failure means no attempt."""
    folder = folder_for(root, key)
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        raise PositionConsumed(key, status(root, key)["status"]) from None
    record = dict(
        schema=SCHEMA,
        case=key,
        attempt_id=attempt_id or uuid.uuid4().hex,
        claimed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        frozen_head=frozen_head,
        frozen_digest=frozen_digest,
        executing_head=executing_head,
        manifest=manifest,
        budgets=dict(budgets or {}),
        identity_digest=identity_digest,
        initial=initial,
        state="CLAIMED",
    )
    try:
        _write_exclusive(folder / "claim.json", record)
    except OSError as error:
        # The directory already exists, so the position stays consumed even without a record.
        try:
            _write_exclusive(folder / "write_failure.json",
                             dict(case=key, stage="claim", reason=type(error).__name__,
                                  detail=str(error)[:200], at=time.time()))
        except OSError:
            pass
        raise PositionWriteFailed(key, "claim") from None
    _fsync_dir(folder)
    _fsync_dir(folder.parent)
    return record


def start(root, key):
    """Record that execution is about to begin, before the first model or target request."""
    folder = folder_for(root, key)
    if not folder.is_dir():
        raise PositionConsumed(key, "UNCONSUMED")
    payload = dict(schema=SCHEMA, case=key, started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   state="RUNNING")
    try:
        _write_exclusive(folder / "running.json", payload)
    except FileExistsError:
        raise PositionConsumed(key, status(root, key)["status"]) from None
    _fsync_dir(folder)
    return payload


def interrupt(root, key, *, reason, detail=None):
    """Preserve an abnormal end; never overwrite the first explanation."""
    folder = folder_for(root, key)
    if not folder.is_dir():
        return None
    payload = dict(schema=SCHEMA, case=key, reason=reason, detail=(detail or "")[:1000],
                   at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   state="INTERRUPTED_OR_UNKNOWN")
    try:
        _write_exclusive(folder / "interrupted.json", payload)
    except FileExistsError:
        return _read(folder / "interrupted.json")
    _fsync_dir(folder)
    return payload


def complete(root, key, *, verdict, evidence, requests=None, model_calls=None, unknown=None,
             elapsed=None):
    """Store the attempt's evidence, then its completion marker.

    The marker records the SHA-256 of the exact evidence bytes and the values derived from that
    evidence, so a later tamper or a half-written file cannot be read as a completed PASS. The
    evidence file is written first: a crash between the two writes leaves ``RUNNING`` with
    evidence on disk, which is still a consumed position. A failed write is reported and the
    position is marked ``INTERRUPTED_OR_UNKNOWN`` so it can never be retried.
    """
    folder = folder_for(root, key)
    if not folder.is_dir():
        raise PositionWriteFailed(key, "no-claim")
    metrics = evidence.get("metrics") if isinstance(evidence.get("metrics"), dict) else {}
    product = evidence.get("product") if isinstance(evidence.get("product"), dict) else {}
    verified = dict(
        requests=metrics.get("requests"),
        model_calls=product.get("model_calls"),
        unknown="UNKNOWN" in json.dumps(product, ensure_ascii=False),
    )
    text = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    import hashlib

    evidence_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        _write_text_exclusive(folder / "evidence.json", text)
    except OSError as error:
        try:
            _write_exclusive(folder / "write_failure.json",
                             dict(case=key, stage="evidence", reason=type(error).__name__,
                                  detail=str(error)[:200],
                                  at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        except OSError:
            pass
        raise PositionWriteFailed(key, "evidence") from None
    record = dict(schema=SCHEMA, case=key, verdict=verdict, requests=verified["requests"],
                  model_calls=verified["model_calls"], unknown=verified["unknown"],
                  elapsed=elapsed, evidence="evidence.json",
                  evidence_sha256=evidence_sha256, evidence_bytes=len(text.encode("utf-8")),
                  completed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), state="COMPLETED")
    try:
        _write_exclusive(folder / "completed.json", record)
    except OSError as error:
        try:
            _write_exclusive(folder / "write_failure.json",
                             dict(case=key, stage="completed", reason=type(error).__name__,
                                  detail=str(error)[:200],
                                  at=time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        except OSError:
            pass
        raise PositionWriteFailed(key, "completed") from None
    _fsync_dir(folder)
    return record


def summary(root, cases=(), consumed_positions=()):
    """Read-only view over the position records and any historical result file.

    Nothing here starts a service, calls a model or sends a request; it may be used after a
    crash, or when ``result.json`` was never written.
    """
    history = legacy_evidence(root)
    rows = {}
    for key in cases:
        rows[key] = status(root, key, consumed_positions=consumed_positions)
        if history.get("signals", {}).get(key):
            rows[key]["legacy"] = history["signals"][key]
    return dict(
        schema=SCHEMA,
        folder=str(Path(root)),
        result_json=dict(
            present=(Path(root) / "result.json").is_file(),
            corrupt=bool(history.get("corrupt")),
            requests=history.get("requests") or {},
            verdicts=history.get("verdicts") or {},
        ),
        positions=rows,
        runnable=[key for key, row in rows.items() if row["status"] == "UNCONSUMED"],
        consumed=[key for key, row in rows.items() if row["status"] != "UNCONSUMED"],
    )


def write_summary(root, *, cases=(), consumed_positions=()):
    """Persist one new immutable summary generation; never replace an older one."""
    folder = Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    payload = dict(summary(folder, cases=cases, consumed_positions=consumed_positions),
                   generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    for index in range(1, 1000):
        path = folder / f"summary-{index}.json"
        try:
            _write_exclusive(path, payload)
        except FileExistsError:
            continue
        return path
    raise PositionWriteFailed(",".join(cases), "summary")
