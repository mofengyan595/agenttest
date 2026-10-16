"""Operator-owned fixture for three disposable ticket regression examples only."""

from pathlib import Path

import pytest

from agenttest.regression import RegressionRuntime
from agenttest.report import ReportStore
from agenttest.ticket_fault_check import CASE_IDS, case_inputs


def pytest_addoption(parser):
    parser.addoption("--agenttest-ticket-faults", action="store_true", default=False,
                     help="仅给三个进程内样例注入既有工单故障，预期 pytest 失败")


@pytest.fixture
def agenttest_regression_runtime(request, tmp_path):
    case_id = Path(str(request.fspath)).parent.name.upper()
    if case_id not in CASE_IDS:
        raise ValueError("Unknown disposable regression example")
    document, target, registry, environ, transport, task, _, _ = case_inputs(
        case_id, request.config.getoption("--agenttest-ticket-faults"))
    return RegressionRuntime(document, target, registry, task, environ, transport,
                             ReportStore(tmp_path / "reports"))
