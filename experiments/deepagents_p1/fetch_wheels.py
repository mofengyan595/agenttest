"""Fetch exact wheels for one experiment venv when pip's downloader stalls.

``pip index versions deepagents`` answers in seconds here, and ``pip download html5lib``
finishes in ~5s, yet ``pip download deepagents==0.7.35`` never completes in this
environment. The PyPI JSON API is fast, so this helper resolves the dependency graph from
that API and downloads the chosen wheels, which are then installed with
``pip install --no-index --no-deps`` (no resolver, no network in pip).

It is a development utility for the P1 admission test only: it is not imported by the
product, it writes only into ``.eduwork/wheels``, and it never installs anything itself.

Usage::

    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/fetch_wheels.py \\
        --root deepagents==0.7.35 --root langchain==1.4.10 --print-plan

Add ``--download`` to actually fetch the wheels.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from dataclasses import dataclass

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

REPO = pathlib.Path(__file__).resolve().parents[2]
WHEEL_DIR = REPO / ".eduwork" / "wheels"
# The experiment venv: Windows, CPython 3.11. Keep this in sync with .python-version.
ENVIRONMENT = {
    "python_version": "3.11",
    "python_full_version": "3.11.15",
    "sys_platform": "win32",
    "platform_system": "Windows",
    "platform_machine": "AMD64",
    "os_name": "nt",
    "implementation_name": "cpython",
    "extra": "",
}
WHEEL_PRIORITY = ("py3-none-any", "py2.py3-none-any", "cp311-cp311-win_amd64",
                  "cp311-abi3-win_amd64", "cp311-none-win_amd64", "none-win_amd64")


@dataclass
class Project:
    name: str
    version: Version
    wheel: dict


def _best_wheel(files: list[dict]) -> dict | None:
    """Pick a Windows/CPython-3.11 compatible wheel; never fall back to an sdist."""
    candidates = [item for item in files
                  if item["filename"].endswith(".whl") and not item.get("yanked")]
    for tag in WHEEL_PRIORITY:
        for item in candidates:
            if item["filename"].endswith(f"-{tag}.whl"):
                return item
    for item in candidates:
        name = item["filename"].lower()
        if "win_amd64" in name or "none-any" in name:
            return item
    return None


class Index:
    """Minimal PyPI JSON reader with a per-project cache."""

    def __init__(self, client: httpx.Client):
        self._client = client
        self._cache: dict[str, dict] = {}

    def project(self, name: str) -> dict:
        key = name.lower().replace("_", "-")
        if key not in self._cache:
            response = self._client.get(f"https://pypi.org/pypi/{key}/json", timeout=60.0)
            response.raise_for_status()
            self._cache[key] = response.json()
        return self._cache[key]

    def release_files(self, name: str, version: str) -> list[dict]:
        data = self.project(name)
        if Version(version) == Version(data["info"]["version"]):
            return data["urls"]
        return data["releases"].get(version) or []


def _satisfies(version: str, specifier) -> bool:
    try:
        return specifier.contains(Version(version), prereleases=False)
    except InvalidVersion:
        return False


def resolve(roots: list[str], index: Index, *, limit: int = 120) -> dict[str, Project]:
    """Breadth-first resolution; the newest version satisfying every accumulated bound."""
    constraints: dict[str, list] = {}
    chosen: dict[str, Project] = {}
    queue: list[tuple[str, str | None]] = [(item, None) for item in roots]
    seen: set[tuple[str, str]] = set()
    while queue and len(chosen) < limit:
        raw, _parent = queue.pop(0)
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            raise SystemExit(f"invalid requirement: {raw}") from None
        name = requirement.name
        constraints.setdefault(name.lower(), []).append(requirement.specifier)
        if name.lower() in chosen:
            current = chosen[name.lower()]
            if all(_satisfies(str(current.version), spec) for spec in constraints[name.lower()]):
                continue
            chosen.pop(name.lower())
        files_by_version = index.project(name).get("releases") or {}
        usable = []
        for version in files_by_version:
            if not all(_satisfies(version, spec) for spec in constraints[name.lower()]):
                continue
            wheel = _best_wheel(files_by_version[version])
            if wheel is not None:
                usable.append((Version(version), wheel))
        if not usable:
            raise SystemExit(f"no compatible wheel for {name} {constraints[name.lower()]}")
        version, wheel = max(usable, key=lambda item: item[0])
        chosen[name.lower()] = Project(name=name.lower(), version=version, wheel=wheel)
        for raw_dep in index.project(name)["info"].get("requires_dist") or ():
            try:
                dependency = Requirement(raw_dep)
            except InvalidRequirement:
                continue
            if dependency.marker is not None and not dependency.marker.evaluate(ENVIRONMENT):
                continue
            if dependency.extras:
                continue  # provider extras (aws/quickjs/video) are not part of this test
            key = (dependency.name.lower(), str(dependency.specifier))
            if key in seen:
                continue
            seen.add(key)
            queue.append((f"{dependency.name}{dependency.specifier}", name))
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=[],
                        help="requirement such as deepagents==0.7.35 (repeatable)")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--print-plan", action="store_true")
    parser.add_argument("--out", type=pathlib.Path, default=WHEEL_DIR)
    args = parser.parse_args(argv)
    if not args.root:
        parser.error("at least one --root is required")
    with httpx.Client(follow_redirects=True, trust_env=False) as client:
        index = Index(client)
        plan = resolve(list(args.root), index)
        if args.print_plan or not args.download:
            print(json.dumps({name: str(item.version) for name, item in
                              sorted(plan.items())}, indent=2))
        if not args.download:
            return 0
        args.out.mkdir(parents=True, exist_ok=True)
        for name, item in sorted(plan.items()):
            target = args.out / item["filename"] if isinstance(item, dict) else (
                args.out / item.wheel["filename"])
            if target.exists() and target.stat().st_size == item.wheel["size"]:
                print(f"cached {target.name}")
                continue
            started = time.monotonic()
            with client.stream("GET", item.wheel["url"], timeout=180.0) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes(65536):
                        handle.write(chunk)
            print(f"fetched {target.name} ({target.stat().st_size} bytes, "
                  f"{time.monotonic() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
