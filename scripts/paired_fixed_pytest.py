"""Fixed pytest arm: explicit HTTP assertions; assigned defects intentionally fail pytest."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from agenttest.paired_baseline import fixed_case
from agenttest.paired_tasks import paired_cases


@pytest.mark.parametrize("round_index,case", [(r, c) for r in range(1, 4) for c in paired_cases()],
                         ids=[f"r{r}-{c['id']}" for r in range(1, 4) for c in paired_cases()])
def test_fixed_http(round_index, case):
    result = asyncio.run(fixed_case(case))
    healthy = asyncio.run(fixed_case(case, healthy=True)) if case["fault"] != "none" else None
    row = {"case_id": case["id"], "round": round_index, **result, "healthy": healthy}
    output = Path(os.environ["AGENTTEST_PAIRED_OUTPUT"])
    (output / f"fixed-{round_index}-{case['id']}.json").write_text(
        json.dumps(row), encoding="utf-8")
    assert result["outcome"] in {"PASS", "SAFE_REJECTION"}
