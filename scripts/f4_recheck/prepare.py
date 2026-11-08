"""Prepare, preflight and freeze a new schedule without making a model request."""

import argparse
import ast
import asyncio
import json
import secrets
import shutil
import stat
import subprocess
import sys
import zipfile
from importlib.metadata import distributions
from pathlib import Path

import httpx
from budget import cap
from fixture import ALIASES, FACTS, PORT, configs, encoded, sha, tasks

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PLAN = ROOT / "docs/f4-recheck-plan-20261108.md"
VERSIONS = dict(
    baseline="8111a37358f1cc161249016035ff0c25c68ea0ca",
    candidate="a779ca56fde7a60228a5804425f045964503d2f2",
)


def write(path, value):
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def manifest(tree):
    return {
        p.relative_to(tree).as_posix(): sha(p.read_bytes())
        for p in sorted(tree.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def init(folder):
    assert git("branch", "--show-current").decode().strip() == "master"
    folder.mkdir()
    (folder / "private").mkdir()
    from agenttest.llm_planner import ModelSettings

    model = ModelSettings.from_env_file(ROOT / ".env", environ={}).model_copy(
        update={"model": "ecnu-max"}
    )
    assert model.protocol == "chatecnu", (
        "The configured ECNU protocol must explicitly send temperature=0"
    )
    private_tokens = {alias: secrets.token_urlsafe(32) for alias in ALIASES}
    write(folder / "private/fixture-tokens.json", private_tokens)
    env = [
        "AGENTTEST_MODEL=ecnu-max",
        "OPENAI_BASE_URL=" + model.base_url,
        "OPENAI_API_KEY=" + model.api_key.get_secret_value(),
    ]
    env.extend(
        "AGENTTEST_TARGET_F4_" + k.upper() + "_TOKEN=" + v for k, v in private_tokens.items()
    )
    (folder / "private/model.env").write_bytes(("\n".join(env) + "\n").encode())
    write(folder / "configs.json", configs())
    items = tasks(PLAN)
    # Proposal is allowed to contain these texts; check prior actual frozen schedules only.
    previous = []
    for archive in (ROOT / "docs/evidence").glob("**/*.zip"):
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if name.endswith("freeze.json"):
                    previous.append(bundle.read(name).decode("utf-8", errors="replace"))
    for old in (ROOT / "docs/evidence").glob("**/freeze.json"):
        previous.append(old.read_text(encoding="utf-8", errors="replace"))
    assert not any(item["request"] in old for item in items for old in previous)
    write(folder / "tasks.json", items)
    write(folder / "active.json", {"id": "_idle"})
    metadata = dict(
        root_head=git("rev-parse", "HEAD").decode().strip(),
        versions=VERSIONS,
        model=model.model_dump(mode="json", exclude={"api_key", "base_url"}),
        endpoint_sha256=sha(model.base_url.encode()),
        plan_sha256=sha(PLAN.read_bytes()),
        protocol="12 first positions, no repair/replay; ordinary Web UI; F4 recheck only",
        token_budget_decision="Before paid calls: retain BOTH native 500000-token suite budgets "
        "and native per-call byte-bound reservations. Proposed 64000 conflicts with native "
        "68192 reservation; not applied. Max four model calls, eight target HTTP, 180 seconds "
        "remain fixed. No budget increase or retuning after a paid position.",
        freshness="3 exact texts absent from every prior actual frozen schedule; new fixture "
        "domain, audience and labels; each text checked against the deterministic "
        "extractors and against its own rubric before freeze.",
        authorization="2026-11-08 operator explicitly authorizes this 12-position real ecnu-max "
        "F4 recheck without repeated ordinary-stage approval; UNKNOWN/infra/drift stop.",
        dependencies=sorted((d.metadata["Name"], d.version) for d in distributions()),
    )
    write(folder / "preparation.json", metadata)
    changed = (
        git("diff", "--name-only", VERSIONS["baseline"], VERSIONS["candidate"], "--", "src", "web")
        .decode()
        .splitlines()
    )
    assert set(changed) == {
        "src/agenttest/diagnostics.py",
        "src/agenttest/plan_construction.py",
        "src/agenttest/product_design.py",
        "src/agenttest/product_focus.py",
        "src/agenttest/product_tasks.py",
        "src/agenttest/request_requirements.py",
        "src/agenttest/requirement_ledger.py",
    }, sorted(changed)
    for arm, commit in VERSIONS.items():
        target = folder / ("source-" + arm)
        archive = folder / ("source-" + arm + ".zip")
        subprocess.run(
            ["git", "archive", "--format=zip", f"--output={archive}", commit], cwd=ROOT, check=True
        )
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    print("Prepared 3 untouched proposal texts, two exact Git archives; no model calls.")


def schemas(folder, arm):
    source = folder / ("source-" + arm)
    sys.path.insert(0, str(source / "src"))
    import agenttest
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, build_generic_project
    from agenttest.goal_server import target_environment
    from agenttest.llm_planner import ModelSettings
    from agenttest.product_design import TestDesignModel, design_groups, propose_design
    from agenttest.product_focus import TestFocusModel, select_focus
    from agenttest.project_inventory import inventory

    assert Path(agenttest.__file__).resolve().is_relative_to(source / "src")
    env = target_environment(folder / "private/model.env", {})
    settings = ModelSettings.from_env_file(folder / "private/model.env", environ={})
    catalog = json.loads((folder / "configs.json").read_text(encoding="utf-8"))
    items = json.loads((folder / "tasks.json").read_text(encoding="utf-8"))
    projects, authority, payloads = {}, {}, {}
    for key, cfg in catalog.items():
        setup = GenericSetup.model_validate(cfg["generic"])
        project = cap(
            build_generic_project(
                "f4_" + key, cfg["document"], load_openapi(cfg["document"]), setup, environ=env
            )
        )
        assert not project.target.allow_mutations and project.target.max_requests == 8
        projects[key] = project
        authority[key] = dict(
            operations={
                o.operation_id: dict(method=o.method, path=o.path)
                for o in project.contract.operations
            },
            policies=[p.model_dump(mode="json") for p in project.registry.definitions()],
            budget=project.registration.budget.model_dump(mode="json"),
            ready=inventory(project.contract, setup, env),
        )

    class Captured(Exception):
        pass

    class Capture:
        async def generate(self, context):
            self.context = context
            raise Captured()

    async def collect():
        for item in items:
            project, cfg = projects[item["config"]], catalog[item["config"]]
            model = Capture()
            try:
                await select_focus(
                    project,
                    model,
                    dict(current_request=item["request"], requests=[item["request"]]),
                    environ=env,
                    inventory=authority[item["config"]]["ready"],
                    available_identity_aliases=list(ALIASES),
                )
            except Captured:
                pass
            configured = settings.model_copy(
                update={
                    k: getattr(project.registration.budget, k)
                    for k in ("max_input_tokens", "max_output_tokens", "max_total_tokens")
                }
            )
            payload = TestFocusModel(configured, profile="generic").prepare_request(model.context)
            payloads[item["id"]] = json.loads(payload)
            for number, group in enumerate(design_groups(project)):
                probe = Capture()
                try:
                    await propose_design(
                        project,
                        item["request"],
                        group,
                        probe,
                        environ=env,
                        remaining_requests=8,
                        inventory_total=len(cfg["generic"]["operations"]),
                        available_identity_aliases=list(ALIASES),
                    )
                except Captured:
                    pass
                payloads[item["id"] + f".design{number}"] = json.loads(
                    TestDesignModel(configured, profile="generic").prepare_request(probe.context)
                )

    asyncio.run(collect())
    write(folder / (arm + "-authority.json"), authority)
    write(folder / (arm + "-offline-requests.json"), payloads)
    sizes = {key: len(encoded(value)) for key, value in payloads.items()}
    print(
        arm,
        "compiled",
        len(projects),
        "projects;",
        len(payloads),
        "offline payloads; largest JSON bytes:",
        max(sizes.values()),
    )


def preflight(folder):
    cfgs = json.loads((folder / "configs.json").read_text(encoding="utf-8"))
    tokens = json.loads((folder / "private/fixture-tokens.json").read_text())
    expected_state = sha(encoded(FACTS))
    rows = []
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        assert (
            client.get(f"http://127.0.0.1:{PORT}/__state").json()["state_sha256"] == expected_state
        )
        for op, (path, _, role, body) in FACTS.items():
            path = path.format(registerId=31, entryId=58, voucherId=41, rosterId=8)
            for actor in dict.fromkeys([role, "overseer", None]):
                (folder / "active.json").write_bytes(encoded({"id": "_preflight"}))
                response = client.get(
                    f"http://127.0.0.1:{PORT}" + path,
                    headers={"Authorization": "Bearer " + tokens[actor]} if actor else {},
                )
                expected = 200 if actor == role else 401 if actor is None else 403
                assert response.status_code == expected and not response.is_redirect, (op, actor)
                if expected == 200:
                    assert response.json() == body
                rows.append(
                    dict(
                        operation=op,
                        role=actor,
                        status=response.status_code,
                        body_sha256=sha(response.content),
                    )
                )
        for arm, port in [("baseline", 19095), ("candidate", 19096)]:
            assert client.get(f"http://127.0.0.1:{port}/health").status_code == 200
            session = client.get(f"http://127.0.0.1:{port}/api/task-session").json()
            for key, cfg in cfgs.items():
                response = client.post(
                    f"http://127.0.0.1:{port}/api/project-intake",
                    json=cfg,
                    headers={
                        "Origin": f"http://127.0.0.1:{port}",
                        "x-agenttest-csrf": session["csrf_token"],
                    },
                )
                assert response.status_code == 201 and response.json()["ready"], (
                    arm,
                    key,
                    response.status_code,
                )
                rows.append(
                    dict(
                        arm=arm,
                        config=key,
                        intake_status=response.status_code,
                        project_id=response.json()["id"],
                    )
                )
    (folder / "active.json").write_bytes(encoded({"id": "_idle"}))
    write(folder / "preflight.json", rows)
    print(
        "Actual preflight passed:", len(rows), "identity/response/intake checks, zero model calls"
    )


def freeze(folder):
    assert not (folder / "schedule-claim.json").exists()
    assert (
        git("rev-parse", "HEAD").decode().strip()
        == json.loads((folder / "preparation.json").read_bytes())["root_head"]
    )
    sources = {}
    for arm in VERSIONS:
        target = folder / ("source-" + arm)
        if not (target / "web/dist").exists():
            shutil.copytree(folder / "shared-web-dist", target / "web/dist")
        assert manifest(target / "web/dist") == manifest(folder / "shared-web-dist")
        # Only caches generated by this preparation, inside the new snapshot, may be removed.
        for cache in list(target.rglob("__pycache__")):
            assert cache.resolve().is_relative_to(target.resolve()) and not cache.is_symlink()
            assert cache.name == "__pycache__" and cache.is_dir()
            shutil.rmtree(cache)
        sources[arm] = manifest(target)
        for path in target.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
    write(folder / "source-manifest.json", sources)
    authorities = [
        json.loads((folder / (arm + "-authority.json")).read_bytes()) for arm in VERSIONS
    ]
    assert authorities[0] == authorities[1], "Approval or budget asymmetry"
    data = json.loads((folder / "preparation.json").read_bytes())
    data.update(
        configs=json.loads((folder / "configs.json").read_bytes()),
        tasks=json.loads((folder / "tasks.json").read_bytes()),
        authority=authorities[0],
        preflight=json.loads((folder / "preflight.json").read_bytes()),
        browser_preflight=json.loads((folder / "browser-preflight.json").read_bytes()),
        per_stage_parameters={
            stage: dict(
                model="ecnu-max",
                temperature=0,
                max_input_tokens=60000,
                max_output_tokens=8192,
                max_total_tokens=70000,
                timeout=timeout,
            )
            for stage, timeout in [("focus", 90), ("design", 120), ("probe", 90)]
        },
        ports=dict(target=PORT, baseline=19095, candidate=19096),
        rounds=2,
        target_state_sha256=sha(encoded(FACTS)),
        pre_paid_execution_checks={
            arm: dict(
                execution={
                    name: sha(
                        (folder / (arm + "-execution-preflight-" + name + ".json")).read_bytes()
                    )
                    for name in ("chain", "review")
                },
                scorer=sha((folder / (arm + "-scorer-preflight.json")).read_bytes()),
            )
            for arm in VERSIONS
        },
        scorer_test_junit_sha256=sha((folder / "evaluation-tests.xml").read_bytes()),
        source_archives={
            arm: sha((folder / ("source-" + arm + ".zip")).read_bytes()) for arm in VERSIONS
        },
        scoring_protocol=dict(
            full_denominator_per_arm=6,
            executable_tasks=1,
            held_tasks=2,
            complete_denominator_per_arm=2,
            hold_denominator_per_arm=4,
            incorrect_global_refusals_include_zero_request_and_partial_hold=True,
            exact_policy_role_selector_and_actual_binding_required=True,
            three_scope_counters="configured authority / current task / pending scope",
            stop=(
                "source or target drift, UNKNOWN usage, infrastructure or "
                "configured authority violation"
            ),
            acceptance="all 12; false refusal not higher; silence not increased; "
            "G2 correct hold in both rounds; all candidate scope counters zero; "
            "no unknown",
            first_results_only=True,
            model_retries=0,
        ),
        limits=dict(
            max_model_calls=4, max_requests=8, max_seconds=180, suite_max_total_tokens=500000
        ),
        independent_human_review="NOT_PERFORMED; development-side cold recomputation only",
        evaluator_sha256={
            p.name: sha(p.read_bytes())
            for p in sorted(HERE.iterdir())
            if p.suffix in {".py", ".mjs"}
        },
        root_files={
            name: sha((ROOT / name).read_bytes())
            for name in git("ls-files", "--cached", "--others", "--exclude-standard")
            .decode()
            .splitlines()
            if (ROOT / name).is_file()
        },
        runtime_prompt_schema_sha256={
            arm: sha((folder / (arm + "-offline-requests.json")).read_bytes()) for arm in VERSIONS
        },
        prompt_constants={arm: prompts(folder / ("source-" + arm)) for arm in VERSIONS},
    )
    write(folder / "freeze.json", data)
    (folder / "freeze.sha256").write_bytes(
        (sha((folder / "freeze.json").read_bytes()) + "\n").encode()
    )
    for name in (
        "freeze.json",
        "freeze.sha256",
        "source-manifest.json",
        "tasks.json",
        "configs.json",
    ):
        (folder / name).chmod(stat.S_IREAD)
    print("Frozen 12 FIRST positions:", sha((folder / "freeze.json").read_bytes()))


def prompts(source):
    result = {}
    for path in (source / "src/agenttest").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "PROMPT" in target.id:
                        result[path.name + ":" + target.id] = sha(node.value.value.encode())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["init", "schemas", "preflight", "freeze"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--arm", choices=list(VERSIONS))
    args = parser.parse_args()
    if args.mode == "schemas":
        schemas(args.folder.resolve(), args.arm)
    else:
        globals()[args.mode](args.folder.resolve())
