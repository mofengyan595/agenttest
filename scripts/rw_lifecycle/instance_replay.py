"""Offline replay of captured Focus replies against the current instance rules.

The replay never calls a model and never sends HTTP: it reads the archived v3 evidence, resolves
every recorded ``before`` edge to concrete instances with the current code, and reports whether the
resulting dependency graph is acyclic. That is what a run would have constructed, so a false cycle
here is a false cycle before spending a position.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttest.request_requirements import (  # noqa: E402
    order_ambiguity,
    order_instances,
    request_atoms,
)

EVIDENCE = ROOT / "docs/evidence/rw-lifecycle-20261109-v3/result.json"


def focus_of(row):
    return next((item for item in row.get("focus") or [] if item.get("content")), None)


def clauses(wire):
    requirements = []
    for source_id, entry in (wire.get("requirement_map") or {}).items():
        requirements.append(dict(source_id=source_id, disposition="required",
                                 actions=entry.get("actions") or [],
                                 before=entry.get("before") or [],
                                 same_resource=entry.get("same_resource") or [],
                                 response_checks=[]))
    return requirements


def edge_graph(requirements, text):
    """Resolve recorded edges the way construction does; report the topological outcome."""
    atoms = {atom["source_id"]: atom for atom in request_atoms(text)}
    nodes, edges, refused = [], {}, []
    for requirement in requirements:
        for action in requirement["actions"]:
            node = (requirement["source_id"], action.get("requirement_action_index", 0))
            nodes.append(node)
    for requirement in requirements:
        for order in requirement["before"]:
            try:
                earlier, later = order_instances(order, requirements, atoms)
            except Exception as error:  # noqa: BLE001 - the diagnostic is the evidence
                diagnostic = getattr(error, "diagnostic", None)
                refused.append(dict(order=[order.get("earlier"), order.get("later")],
                                    code=getattr(diagnostic, "code", type(error).__name__)))
                continue
            for target in later:
                for source in earlier:
                    if source == target:
                        refused.append(dict(order=[order.get("earlier"), order.get("later")],
                                            code="SELF_EDGE"))
                        continue
                    edges.setdefault(target, set()).add(source)
    # Kahn over "target depends on source".
    remaining = {node: set(sources) for node, sources in edges.items()}
    resolved = [node for node in nodes if node not in remaining]
    changed = True
    while changed:
        changed = False
        for node in list(remaining):
            if not remaining[node]:
                resolved.append(node)
                del remaining[node]
                for other in remaining.values():
                    other.discard(node)
                changed = True
    return dict(nodes=[list(node) for node in nodes], edges=len(edges),
                order_edges=sum(len(sources) for sources in edges.values()),
                cycle_nodes=[list(node) for node in sorted(remaining)],
                refused=refused)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(EVIDENCE))
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    report = dict(evidence=Path(args.evidence).name, cases=[])
    for row in data["results"]:
        focus = focus_of(row)
        entry = dict(case=row["case"], verdict=row["verdict"], requests=row.get("requests"),
                     focus=bool(focus))
        if not focus:
            report["cases"].append(entry)
            continue
        wire = json.loads(focus["content"])
        requirements = clauses(wire)
        text = row.get("text") or ""
        graph = edge_graph(requirements, text)
        entry.update(clauses=len(requirements), explicit_indices=any(
            order.get("earlier_index") is not None or order.get("later_source_id") is not None
            for requirement in requirements for order in requirement["before"]),
            operations={requirement["source_id"]:
                        [action.get("operation_id") for action in requirement["actions"]]
                        for requirement in requirements},
            graph=graph)
        if text:
            ambiguity = order_ambiguity(
                requirements, {atom["source_id"]: atom for atom in request_atoms(text)})
            entry["ambiguity"] = (dict(clause=ambiguity["clause"],
                                       earlier=len(ambiguity["earlier"]),
                                       later=len(ambiguity["later"]))
                                  if ambiguity else None)
        report["cases"].append(entry)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.json:
        Path(args.json).write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
