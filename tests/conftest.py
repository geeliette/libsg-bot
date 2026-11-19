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


@pytest.fixture
def pp_search():
    """Pride and prejudice search; contains brn-0 placeholder records."""
    return load("search_titles_pride-and-prejudice.json")


@pytest.fixture
def branches_data():
    return load("get_branches_onsite.json")


@pytest.fixture
def alice_search():
    """Alice's adventures in wonderland: one work with 19 print editions (needs paging)."""
    return load("search_titles_alice-in-wonderland_bks.json")
