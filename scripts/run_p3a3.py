"""Run the frozen P3-A.3 paired and independent acceptance on disposable labs."""

import argparse
import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_p3a2 import ADAPTIVE, BATCH_A, BATCH_B, FIXED, ROOT, command

FRESH = [
    dict(key="fresh-crud", kind="parcels",
         text="请验收包裹的新增、查询、修改和删除，确认同一包裹的状态。"),
    dict(key="fresh-delete", kind="parcels", delete_noop=True,
         text="清理一个本轮新建的包裹后再查它。请验证它已经无法读取。"),
    dict(key="fresh-investigation", kind="directory",
         text="目录的可用性检查出现错误，请实际复现一次，记录返回状态和证据。"),
    dict(key="fresh-release", kind="parcels",
         text="发布前请跑一轮完整验收，包含已确认的正常流程及非法入参。"),
    dict(key="fresh-boundary", kind="parcels",
         text="请核验包裹提交的数量边界，覆盖已批准的无效数量校验。"),
    dict(key="fresh-ambiguous", kind="directory", text="帮我处理一下。"),
]


def source_fingerprint():
    digest = hashlib.sha256()
    for directory in ('src', 'scripts'):
        for path in sorted((ROOT / directory).rglob('*.py')):
            digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0')
            digest.update(path.read_bytes())
    return digest.hexdigest()


def schedule():
    for batch, tasks in (("A", BATCH_A), ("B", BATCH_B), ("C", FRESH)):
        for task in tasks:
            modes = (FIXED, ADAPTIVE) if batch == "A" else (ADAPTIVE,)
            for repeat in range(1, 4):
                for mode in modes:
                    yield dict(task, batch=batch, mode=mode, repeat=repeat,
                               id=f"{task['key']}-{mode.lower()}-r{repeat}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-port", type=int, default=22600)
    parser.add_argument("--jobs", type=int, choices=[1, 2, 3], default=1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    frozen = source_fingerprint()
    rows = list(schedule())
    (args.out / "manifest.json").write_text(json.dumps(
        dict(source_sha256=frozen, started_at=time.time(), jobs=args.jobs, positions=rows),
        ensure_ascii=False, indent=2), encoding="utf-8")
    def run_position(item):
        index, row = item
        if source_fingerprint() != frozen:
            raise SystemExit("FROZEN_RUNTIME_CHANGED")
        identifier = row["id"]
        run_dir = args.out / row["batch"] / identifier
        # Same authority, model window and hard ceilings across arms. The FIXED arm
        # receives its allowance up front. ADAPTIVE starts lower and can grow.
        model_base = "64" if row["mode"] == FIXED else "16"
        token_base = "650000" if row["mode"] == FIXED else "180000"
        argv = command(row, f"{row['batch']}:{identifier}", run_dir,
                       mode=ADAPTIVE, port=args.start_port + index * 8)
        argv += ["--execution-mode", row["mode"], "--execution-model-base", model_base,
                 "--execution-token-base", token_base, "--max-model-calls", "64",
                 "--execution-model-ceiling", "64", "--execution-token-ceiling", "650000"]
        with (args.out / f"{row['batch']}-{identifier}.log").open("wb") as log:
            process = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                     check=False)
        return dict(row, exit_code=process.returncode)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool, (
            args.out / "terminal.jsonl").open("a", encoding="utf-8") as output:
        for index, row in enumerate(pool.map(run_position, enumerate(rows)), start=1):
            print(f"{index}/{len(rows)} {row['id']} exit={row['exit_code']}", flush=True)
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()


if __name__ == "__main__":
    main()
