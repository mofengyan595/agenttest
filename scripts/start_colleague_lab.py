"""Start three disposable loopback targets and the persistent product workspace."""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from serve_colleague_targets import document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18120,
                        help="Product port; next three are targets")
    parser.add_argument("--data", type=Path, default=Path("runs/testing-colleague/playground"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65532:
        raise ValueError("Four local ports between 1024 and 65535 required")
    # Check all ports before starting any process; never stop another service.
    sockets = []
    try:
        for port in range(args.port, args.port + 4):
            probe = socket.socket()
            sockets.append(probe)
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit("端口已占用，请用 --port 指定另一组连续四个端口。") from None
    finally:
        for probe in sockets:
            probe.close()
    repository = Path(__file__).resolve().parents[1]
    data = (repository / args.data).resolve()
    documents = data / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    import json

    for kind in ("parcels", "reservations", "directory"):
        (documents / f"{kind}.openapi.json").write_text(
            json.dumps(document(kind), ensure_ascii=False, indent=2), encoding="utf-8")
    environment = dict(os.environ, PYTHONUTF8="1", AGENTTEST_LAB_INSTANCE_ID=uuid4().hex,
                       AGENTTEST_PROJECTS_FILE="", AGENTTEST_SYNTHETIC_TARGETS="0")
    for role in ("OWNER", "READER"):
        for credential in ("USERNAME", "PASSWORD", "BEARER"):
            environment[f"AGENTTEST_TARGET_LAB_{role}_{credential}"] = secrets.token_urlsafe(24)
    children = []

    def start(script, *arguments):
        script_path = repository / "scripts" / script
        child = subprocess.Popen([sys.executable, "-X", "utf8", str(script_path),
                                  *map(str, arguments)], cwd=repository, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        children.append(child)

    try:
        for offset, kind in enumerate(("parcels", "reservations", "directory"), 1):
            start("serve_colleague_targets.py", "--project", kind, "--port", args.port + offset)
        start("serve_web_workspace.py", "--port", args.port, "--data", data / "workspace")
        print(f"网页：http://127.0.0.1:{args.port}", flush=True)
        print(f"接口文档：{documents}", flush=True)
        print("包裹 / 预约 / 目录测试地址端口：" +
              " / ".join(str(args.port + i) for i in (1, 2, 3)), flush=True)
        print("这是自建隔离服务。关闭本命令会停止本次启动的四个服务；"
              "项目记录保存在本地。", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise SystemExit("一个服务已停止，停止本次启动的其他服务。")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
