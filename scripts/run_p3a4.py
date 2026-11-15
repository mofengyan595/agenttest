"""Run P3-A.4 acceptance against separately prepared disposable targets."""

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_p3a2 import ADAPTIVE, ROOT, command
from run_p3a3 import source_fingerprint

DELETE = "新建一个测试包裹并删除，随后回读同一包裹，检查它是否已不存在。"
RELEASE = "服务即将发布，请运行完整的上线前检查，把已确认正常行为和无效参数用例都测到。"


def schedule():
    for prefix, text, fault, observation in (
        ("A-delete", DELETE, False, False),
        ("B-false-204", DELETE, True, False),
        ("C-release", RELEASE, False, True),
    ):
        for repeat in range(1, 4):
            yield dict(key=f"{prefix}-r{repeat}", text=text, kind="parcels",
                       delete_noop=fault, observe_negative_posts=observation)
    yield dict(key="D-fresh-delete", kind="parcels",
               text="先创建一个测试包裹，查询它确认存在，然后删除，最后回读同一个包裹。")
    yield dict(key="D-fresh-false-204", kind="parcels", delete_noop=True,
               text="创建一个隔离测试包裹。删掉这个包裹。再查同一包裹，确认无法取回。")
    yield dict(key="D-fresh-release", kind="parcels", observe_negative_posts=True,
               text="发布前请对整个项目做完整验收，覆盖已批准的正常行为和无效输入。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-port", type=int, default=28600)
    parser.add_argument("--jobs", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument("--release-only", action="store_true")
    args = parser.parse_args()
    source = source_fingerprint()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   text=True).strip()
    args.out.mkdir(parents=True, exist_ok=False)
    tasks = list(schedule())
    if args.release_only:
        tasks = [task for task in tasks if task["key"].startswith("C-")]
        tasks.append(dict(key="D-new-release", kind="parcels", observe_negative_posts=True,
            text="上线前，对项目的全部接口做一次完整验收，覆盖已批准正向流程和非法入参。"))
    (args.out / "manifest.json").write_text(json.dumps(
        dict(head=head, source_sha256=source, positions=tasks, jobs=args.jobs),
        ensure_ascii=False, indent=2), encoding="utf-8")

    def run(item):
        index, task = item
        if source_fingerprint() != source:
            raise RuntimeError("FROZEN_RUNTIME_CHANGED")
        key = task["key"]
        argv = command(task, key, args.out / key, mode=ADAPTIVE,
                       port=args.start_port + 8 * index)
        argv += ["--execution-model-base", "16", "--execution-token-base", "180000",
                 "--max-model-calls", "64", "--execution-model-ceiling", "64",
                 "--execution-token-ceiling", "650000"]
        if task.get("observe_negative_posts"):
            argv.append("--observe-negative-posts")
        with (args.out / f"{key}.log").open("wb") as log:
            process = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     check=False, env=dict(os.environ, PYTHONUTF8="1",
                                                           LANGSMITH_TRACING="false"))
        return dict(id=key, exit_code=process.returncode)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool, (
            args.out / "terminal.jsonl").open("a", encoding="utf-8") as log:
        for result in pool.map(run, enumerate(tasks)):
            print(result, flush=True)
            log.write(json.dumps(result) + "\n")
            log.flush()
    if source_fingerprint() != source:
        raise RuntimeError("FROZEN_RUNTIME_CHANGED")


if __name__ == "__main__":
    main()
