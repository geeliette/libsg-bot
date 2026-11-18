import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def emma_search():
    return load("search_titles_emma-jane-austen.json")


@pytest.fixture
def emma_avail():
    return load("get_availability_brn-300172891.json")


@pytest.fixture
def reference_avail():
    return load("get_availability_brn-4171279.json")


@pytest.fixture
def austen_titles():
    return load("get_titles_author-jane-austen.json")
