import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app


def main() -> None:
    destination = Path(__file__).resolve().parents[3] / "docs" / "api-contracts" / "webapp-openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
