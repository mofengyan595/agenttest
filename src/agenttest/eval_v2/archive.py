"""Seal completed offline evidence; hashes detect replacement, not a malicious OS owner."""

import hashlib
import json
import math
import os
import re
import stat

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.evaluation import save_evaluation

MAX_ARCHIVE_BYTES = 5_000_000
MAX_ARCHIVE_NODES = 100_000


class UnverifiableArchive(ValueError):
    pass


def _regular(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked archive evidence is not accepted")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Invalid archive evidence file")


def read_json(path):
    """Read bounded local evidence, whose repeated contexts exceed a model DSL's size.

    Model/DSL parsing keeps its independent 20,000-node budget. Archive metadata
    keeps duplicate-key, finite-number, depth, link and byte checks as before.
    """
    _regular(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValidationFailure(Layer.JSON, "JSON_SIZE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationFailure(Layer.JSON, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValidationFailure(Layer.JSON, "JSON_NONFINITE_NUMBER")

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (json.JSONDecodeError, RecursionError):
        raise ValidationFailure(Layer.JSON, "JSON_SYNTAX") from None
    pending, nodes = [(data, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if (nodes > MAX_ARCHIVE_NODES or depth > 40
                or type(value) is float and not math.isfinite(value)):
            raise ValidationFailure(Layer.JSON, "JSON_STRUCTURE_LIMIT")
        if isinstance(value, (dict, list)):
            pending.extend((v, depth + 1) for v in (
                value.values() if isinstance(value, dict) else value))
    return data


def inventory(directory):
    files = {}
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in dirs:
            path = directory / os.path.relpath(os.path.join(current, name), directory)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked archive directory")
        for name in names:
            if name == "archive.json" or not (name.endswith(".json") or name == "summary.md"):
                continue
            path = directory / os.path.relpath(os.path.join(current, name), directory)
            _regular(path)
            files[path.relative_to(directory).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return files


def seal(directory):
    files = inventory(directory)
    if not {"freeze.json", "results.json", "summary.md"} <= files.keys():
        raise ValueError("Incomplete evaluation cannot be sealed")
    save_evaluation(
        {"schema_version": "agenttest.eval.archive.v1", "files": files}, directory / "archive.json"
    )


def verify_archive(directory):
    if not (directory / "archive.json").exists():
        raise UnverifiableArchive("UNVERIFIABLE_LEGACY_OR_INCOMPLETE_ARCHIVE")
    manifest = read_json(directory / "archive.json")
    if (
        manifest.get("schema_version") != "agenttest.eval.archive.v1"
        or manifest.get("files") != inventory(directory)
    ):
        raise ValueError("Archived artifact integrity mismatch")
    result, frozen = [read_json(directory / name) for name in ("results.json", "freeze.json")]
    ids = [r["case_id"] for r in result["results"]]
    if len(set(ids)) != len(ids) or any(re.fullmatch(r"C[0-9]{3}", i) is None for i in ids):
        raise ValueError("Duplicate archived case identity")
    for row in result["results"]:
        if read_json(directory / (row["case_id"] + ".json")) != row:
            raise ValueError("Archived case/results identity mismatch")
    for key in ("source", "variant"):
        if result[key] != frozen["evaluation_config"][key] or any(
            r[key] != result[key] for r in result["results"]
        ):
            raise ValueError("Archived provenance/configuration mismatch")
    if result["dataset_version"] != frozen["dataset_version"]:
        raise ValueError("Archived dataset identity mismatch")
    return result, frozen
