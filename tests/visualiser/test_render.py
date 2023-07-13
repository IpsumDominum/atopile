import json
from pathlib import Path

from atopile.model.model import Model
from atopile.visualizer.render import build_view


def test_build_from_root(dummy_model: Model):
    with open(Path(__file__).parent / "expected-circuit.json") as f:
        expected = json.load(f)

    actual = build_view(dummy_model, "dummy_file.ato")

    with open(Path(__file__).parent / "actual-circuit.json", "w") as f:
        json.dump(actual, f, indent=2)

    assert actual == expected