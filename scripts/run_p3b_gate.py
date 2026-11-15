"""Execute the fixed P3-B entry schedule once, on independent disposable labs."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

from run_development_agent import ADAPTIVE_INPUT_BYTES, model_settings
from run_p3a2 import ADAPTIVE, ROOT, command
from run_p3a3 import source_fingerprint
from run_p3a4 import DELETE

from agenttest.evaluation_freeze import public_model_config
from agenttest.report import fingerprint

RELEASE = "上线前请把整个项目完整测一遍，覆盖已批准的正常流程和无效输入。"
FRESH = {
    "crud": "用同一个包裹做一轮冒烟：创建、查询、修改，再删除。",
    "delete": "创建一条临时包裹记录，删除后再查询同一条记录，确认接口返回 404。",
    "investigation": "请调查包裹服务中 readAvailability 的故障，复现并记录实际 HTTP 状态。",
    "release": "发版前请完整验收整个项目，正常路径和非法入参都按已批准的规则执行。",
}
SCORING = {
    "A": "5/5: normalized compiler, exactly POST 201 DELETE 204 GET 404, same owned "
         "identity, proved cleanup, strict completion",
    "B": "5/5: actual approved absence check returns 200 after deletion of the same "
         "identity, ASSERTION_FAILED, cleanup failed and target residue recorded",
    "C": "at least 4/5: requested/selected/approved coverage each 31/31, trusted positive "
         "lifecycle and 503 assertion, all 24 negative policies execute once in their batches, "
         "10 POST observations, all created resources proved clean, strict completion",
    "D": "judge requested actions and recorded evidence per position; inspect earliest "
         "failures for systematic instance mapping errors",
    "always": "retain every position; zero unauthorized requests and unknown write replays",
}


def schedule():
    for repeat in range(1, 6):
        for batch, text in (("A", DELETE), ("B", DELETE), ("C", RELEASE)):
            yield dict(key=f"{batch}-r{repeat}", kind="parcels", text=text,
                       delete_noop=batch == "B", observe_negative_posts=batch == "C")
    for key, text in FRESH.items():
        yield dict(key=f"D-{key}", kind="parcels", text=text,
                   observe_negative_posts=key == "release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-port", type=int, default=29600)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()

    def checkout():
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        tracked = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=ROOT)
        if tracked:
            raise ValueError("Evaluation requires a committed runtime and schedule")
        return head, source_fingerprint()

    def model_config():
        settings = model_settings(ROOT / ".env", 60, max(60000, ADAPTIVE_INPUT_BYTES),
                                  8192, max_total=650000)
        if settings.model != "ecnu-max":
            raise ValueError("Expected the configured ecnu-max model")
        return public_model_config(settings)

    head, source = checkout()
    model = model_config()
    dependencies = sorted((dist.metadata["Name"], dist.version) for dist in distributions())
    tasks = list(schedule())
    positions = []
    extra_budget = ["--execution-model-base", "16", "--execution-token-base", "180000",
                    "--max-model-calls", "64", "--execution-model-ceiling", "64",
                    "--execution-token-ceiling", "650000"]
    for index, task in enumerate(tasks):
        key = task["key"]
        argv = command(task, key, args.out / key, mode=ADAPTIVE, port=args.start_port + 8 * index)
        if task["observe_negative_posts"]:
            argv.append("--observe-negative-posts")
        positions.append(dict(task, argv=argv + extra_budget))

    args.out.mkdir(parents=True, exist_ok=False)
    manifest = dict(head=head, source_sha256=source, model=model, python=sys.version,
                    dependencies=dependencies, dependency_sha256=fingerprint(dependencies),
                    frozen_at=datetime.now(timezone.utc).isoformat(), jobs=args.jobs,
                    scoring=SCORING, positions=positions)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding="utf-8")

    def check_freeze():
        if checkout() != (head, source) or model_config() != model:
            raise RuntimeError("FROZEN_RUNTIME_CHANGED")
        installed = sorted((dist.metadata["Name"], dist.version) for dist in distributions())
        if dependencies != installed:
            raise RuntimeError("FROZEN_DEPENDENCIES_CHANGED")

    def run(position):
        check_freeze()
        key = position["key"]
        with (args.out / f"{key}.log").open("wb") as log:
            process = subprocess.run(position["argv"], cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, check=False,
                                     env=dict(os.environ, PYTHONUTF8="1",
                                              LANGSMITH_TRACING="false"))
        return dict(id=key, exit_code=process.returncode)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool, (
            args.out / "terminal.jsonl").open("a", encoding="utf-8") as terminal:
        for result in pool.map(run, positions):
            print(result, flush=True)
            terminal.write(json.dumps(result) + "\n")
            terminal.flush()
    check_freeze()


if __name__ == "__main__":
    main()
