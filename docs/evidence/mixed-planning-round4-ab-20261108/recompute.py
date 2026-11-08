"""Cold recomputation of the archived round-four positions; no product import."""

import json
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER.parents[2] / "scripts/round4_ab"))
from grade import evaluate  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def score(identifier):
    frozen = read(FOLDER / "freeze.json")
    task = next(t for t in frozen["tasks"] if identifier.startswith(t["id"] + "_"))
    current = FOLDER / "positions" / identifier
    terminal = read(current / "terminal.json")
    reports = [read(current / ("run-" + c["run_id"] + ".json"))
               for c in terminal["summary"].get("cases", []) if c.get("run_id")]
    return evaluate(task, terminal["summary"], reports, frozen["authority"][task["config"]],
                    read(current / "audit.json"), read(current / "observations.json"))


if __name__ == "__main__":
    for record in read(FOLDER / "schedule-terminal.json")["records"]:
        stored = read(FOLDER / "positions" / record["id"] / "score.json")
        assert score(record["id"]) == stored, record["id"]
    print("cold recomputation matched for", len(read(FOLDER / "schedule-terminal.json")["records"]),
          "positions")
