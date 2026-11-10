"""A separate, operator-issued permission for one real acceptance execution.

``authority.WRITE_AUTHORITY`` and the frozen record describe the *maximal approved scope*;
they are not a permission to execute. A real run additionally needs one record created by the
operator, for one case and one commit, through the explicit ``authorize`` command. This module
never creates, upgrades or repairs an authorization on its own: ``verify()`` is read-only and
``authorize()`` only runs when a human invokes the command with every consent value supplied.

The record is an independent audit file under ``<run folder>/authorizations/``; the frozen
declaration, rubric and budgets are never edited. It contains no credential material.
"""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

SCHEMA = 1


class AuthorizationError(Exception):
    """The run may not start; the code names the exact missing or mismatched consent."""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail
        suffix = f":{detail}" if detail else ""
        super().__init__(f"RUN_AUTHORIZATION_{code}{suffix}")


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def folder_for(root):
    return Path(root) / "authorizations"


def _record_integrity(payload):
    body = {key: value for key, value in payload.items() if key != "integrity"}
    return digest(body)


def authorize(root, *, case, executing_head, frozen, operator, allow_model_calls,
              allow_loopback_writes, max_requests, max_model_calls, max_seconds,
              model_timeout, note=None):
    """Write one immutable authorization; every consent is a required explicit argument.

    ``frozen`` is the loaded ``freeze.json``. The budgets must match it exactly: passing the
    old 120-second figure is refused instead of being silently rounded or ignored.
    """
    if not isinstance(operator, str) or not operator.strip():
        raise AuthorizationError("OPERATOR_REQUIRED")
    if not allow_model_calls:
        raise AuthorizationError("MODEL_CALLS_NOT_APPROVED")
    if not allow_loopback_writes:
        raise AuthorizationError("LOOPBACK_WRITES_NOT_APPROVED")
    if not isinstance(executing_head, str) or len(executing_head) != 40:
        raise AuthorizationError("EXECUTING_HEAD_REQUIRED")
    frozen_budgets = dict(frozen.get("budgets") or {})
    declared_timeout = ((frozen.get("authority") or {}).get("declaration") or {}).get(
        "model_timeout")
    supplied = dict(max_requests=max_requests, max_seconds=max_seconds,
                    max_model_calls=max_model_calls)
    if any(value is None for value in supplied.values()):
        raise AuthorizationError("BUDGET_REQUIRED", json.dumps(supplied, sort_keys=True))
    if supplied != frozen_budgets:
        raise AuthorizationError("BUDGET_MISMATCH", json.dumps(
            dict(supplied=supplied, frozen=frozen_budgets), sort_keys=True))
    if model_timeout != declared_timeout:
        raise AuthorizationError("MODEL_TIMEOUT_MISMATCH",
                                 f"supplied={model_timeout} frozen={declared_timeout}")
    frozen_path = Path(root) / "freeze.json"
    if not frozen_path.is_file():
        raise AuthorizationError("FROZEN_MISSING", str(frozen_path))
    frozen_digest = digest(frozen_path.read_bytes())
    payload = dict(
        schema=SCHEMA,
        authorization_id=uuid.uuid4().hex,
        case=case,
        executing_head=executing_head,
        frozen_sha256=frozen_digest,
        operator=operator.strip(),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        allow_model_calls=True,
        allow_loopback_writes=True,
        budgets=supplied,
        model_timeout=model_timeout,
        case_attempts=1,
        statement="仅授权本记录中的位置、提交与预算执行一次；不授权其他 case、不授权重放。",
        note=note,
    )
    payload["integrity"] = _record_integrity(payload)
    folder = folder_for(root)
    folder.mkdir(parents=True, exist_ok=True)
    head7 = executing_head[:7]
    for index in range(1, 1000):
        name = f"authorize-{case}-{head7}" + ("" if index == 1 else f"-{index}") + ".json"
        path = folder / name
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            # The same case, commit and frozen record already has a permission; never replace
            # it. A different commit produces a different file name.
            existing = _read(path)
            if isinstance(existing, dict) and existing.get("frozen_sha256") == frozen_digest:
                raise AuthorizationError("EXISTS_NO_OVERWRITE", path.name) from None
            continue
        return path
    raise AuthorizationError("EXISTS_NO_OVERWRITE", case)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def candidates(root, case):
    folder = folder_for(root)
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"authorize-{case}-*.json"))


def _matching(records, executing_head, frozen_sha256):
    for record in records:
        if (record.get("case") is not None
                and record.get("executing_head") == executing_head
                and record.get("frozen_sha256") == frozen_sha256):
            return record
    return None


def verify(root, *, case, executing_head, frozen_sha256, budgets, model_timeout):
    """Return the single applicable authorization, or raise with the exact reason.

    Read-only: no record is created, upgraded or repaired here.
    """
    paths = candidates(root, case)
    if not paths:
        raise AuthorizationError("MISSING", case)
    records, tampered = [], []
    for path in paths:
        record = _read(path)
        if not isinstance(record, dict) or record.get("integrity") != _record_integrity(record):
            tampered.append(path.name)
            continue
        records.append(record)
    if not records:
        raise AuthorizationError("TAMPERED", ",".join(tampered))
    record = _matching(records, executing_head, frozen_sha256)
    if record is None:
        if any(item.get("executing_head") != executing_head for item in records):
            raise AuthorizationError("HEAD_MISMATCH", executing_head[:12])
        raise AuthorizationError("FROZEN_MISMATCH", frozen_sha256[:12])
    if dict(record.get("budgets") or {}) != dict(budgets or {}):
        raise AuthorizationError("BUDGET_MISMATCH", json.dumps(
            dict(record=record.get("budgets"), frozen=budgets), sort_keys=True))
    if record.get("model_timeout") != model_timeout:
        raise AuthorizationError("MODEL_TIMEOUT_MISMATCH",
                                 f"record={record.get('model_timeout')} frozen={model_timeout}")
    if record.get("allow_model_calls") is not True:
        raise AuthorizationError("MODEL_CALLS_NOT_APPROVED")
    if record.get("allow_loopback_writes") is not True:
        raise AuthorizationError("LOOPBACK_WRITES_NOT_APPROVED")
    if record.get("case") != case:
        raise AuthorizationError("CASE_MISMATCH", str(record.get("case")))
    return record
