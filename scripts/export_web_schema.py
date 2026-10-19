"""Export the actual FastAPI schema without starting a server or reading credentials."""

import json
from pathlib import Path

from agenttest.main import create_app

if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "web" / "api-schema.json"
    destination.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
