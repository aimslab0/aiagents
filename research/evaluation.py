import json
from pathlib import Path


def load_cases():
    return json.loads(Path(__file__).with_name("evaluation_cases.json").read_text(encoding="utf-8"))
