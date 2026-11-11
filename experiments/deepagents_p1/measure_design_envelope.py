"""Offline measurement: how large is the design envelope for each group scope?

The harness model chooses which operations to design for, so the envelope size it can cause is
a real constraint (``ModelSettings.max_input_tokens`` is 20 000 and the planner rejects a
payload above it with ``MODEL_TOKEN_BUDGET``). This script measures that ceiling without
calling a model, so the harness prompt and the P1 report can state the actual limit.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ab_runner import project  # noqa: E402

from agenttest.product_design import design_context, design_groups  # noqa: E402


def main() -> int:
    proj, _, _ = project("http://127.0.0.1:18991")
    text = "帮我测一下包裹管理功能。"
    rows = []
    for operations in (["readAvailability"], ["createParcel"], ["createParcel", "readParcel"],
                       None):
        for group in design_groups(proj, operation_ids=operations):
            context = design_context(proj, text, group, 24, 6)
            size = len(json.dumps(context, ensure_ascii=False).encode("utf-8"))
            rows.append(dict(requested=operations, group=group["id"],
                             primary=group["primary"], closure=len(group["operations"]),
                             payload_bytes=size, under_budget=size + 512 <= 20_000))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
