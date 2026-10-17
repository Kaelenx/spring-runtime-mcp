"""Conformance fixtures are local; the parent autouse fixture blocks sockets."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "fixtures/conformance"
PROFILES = ("boot-2.7-servlet", "boot-3.4-servlet", "boot-4.1-webflux")


@pytest.fixture
def load_sample():
    def load(relative):
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.suffix == ".json"
        return json.loads(path.read_text())

    return load
