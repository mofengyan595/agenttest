"""Do any lab projects already approve an absence expectation (404/410)?

The delete-404 precondition regression needs an *approved* expectation that a path-bound
resource is gone. If no lab project approves one, the scenario cannot be expressed through
trusted configuration and that gap must be reported rather than patched around.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TARGET_SCRIPTS = ROOT / "scripts" / "serve_colleague_targets.py"


def load_targets():
    spec = importlib.util.spec_from_file_location("colleague_targets", TARGET_SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import json as _json  # noqa: E402

from agenttest.contracts import load_openapi  # noqa: E402
from agenttest.generic_intake import (  # noqa: E402
    GenericSetup,
    analyze_contract,
    build_generic_project,
)


def main() -> int:
    targets = load_targets()
    for kind in ("parcels", "reservations", "directory"):
        document = _json.dumps(targets.document(kind), ensure_ascii=False)
        contract = load_openapi(document)
        suggestions = analyze_contract(contract)
        operations = [dict(operation_id=item["operation_id"], alias=item["alias"],
                           statuses=item["statuses"],
                           arguments=[{key: value for key, value in argument.items()
                                       if key in {"location", "name", "value",
                                                  "source_operation", "selector",
                                                  "generated", "needs_input"}}
                                      for argument in item["arguments"]])
                      for item in suggestions]
        project = build_generic_project(kind, document, contract,
            GenericSetup.model_validate(dict(
                base_url="http://127.0.0.1:18991", isolated_confirmed=True,
                allow_mutations=True, assisted=True, approve_negative_tests=False,
                operations=operations)),
            environ={})
        rows = []
        for policy in project.registry.definitions():
            status = policy.assertions.status
            rows.append(dict(policy=policy.id, operation=policy.operation_id,
                             codes=list(status.codes) if status else [],
                             negative=policy.negative.kind if policy.negative else None,
                             prerequisites=list(policy.prerequisite_policy_ids)))
        absence = [row for row in rows if row["codes"] and all(
            code in (404, 410) for code in row["codes"])]
        print(json.dumps(dict(project=kind, policies=rows, absence_expectations=absence),
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
