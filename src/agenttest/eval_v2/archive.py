"""Seal completed offline evidence; hashes detect replacement, not a malicious OS owner."""

import hashlib
import os
import re
import stat

from agenttest.evaluation import save_evaluation
from agenttest.plan_json import bounded_json


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
    _regular(path)
    with path.open("rb") as stream:
        raw = stream.read(5_000_001)
    return bounded_json(raw.decode("utf-8"), 5_000_000)


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
