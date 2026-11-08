"""Own exactly three isolated child processes and stop all of them on exit."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(folder):
    import uvicorn
    from fixture import create_fixture

    if "--fixture" in sys.argv:
        tokens = json.loads((folder / "private/fixture-tokens.json").read_text())
        uvicorn.run(
            create_fixture(folder, tokens),
            host="127.0.0.1",
            port=19091,
            proxy_headers=False,
            access_log=False,
        )
        return
    processes = []
    script = Path(__file__).resolve()
    commands = [("fixture", [sys.executable, str(script), str(folder), "--fixture"], ROOT)]
    for arm, port in [("baseline", 19092), ("candidate", 19093)]:
        source = folder / ("source-" + arm)
        if not (source / "web/dist").exists():
            shutil.copytree(folder / "shared-web-dist", source / "web/dist")
        commands.append(
            (
                arm,
                [
                    sys.executable,
                    str(script.parent / "observe.py"),
                    str(source),
                    "--port",
                    str(port),
                    "--data",
                    str(folder / (arm + "-workspace")),
                    "--model-env",
                    str(folder / "private/model.env"),
                ],
                source,
            )
        )
    try:
        for label, command, cwd in commands:
            with (folder / (label + ".log")).open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env={
                        **{
                            k: v
                            for k, v in os.environ.items()
                            if k not in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "AGENTTEST_MODEL"}
                            and not k.startswith("AGENTTEST_TARGET_R4_")
                        },
                        "PYTHONPATH": str(cwd / "src"),
                        "PYTHONUTF8": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "AGENTTEST_EVAL_OBSERVATIONS": str(folder / (label + "-observations")),
                    },
                )
            processes.append((label, process))
        print(json.dumps({label: p.pid for label, p in processes}), flush=True)
        started = time.monotonic()
        while not (folder / "servers.stop").exists() and time.monotonic() - started < 10800:
            if any(p.poll() is not None for _, p in processes):
                raise RuntimeError("Acceptance child exited; no position replay")
            time.sleep(0.5)
    finally:
        for _, process in reversed(processes):
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                process.wait(timeout=20)
        (folder / "servers-stopped.json").write_text(
            json.dumps({label: p.poll() for label, p in processes}), encoding="utf-8"
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
