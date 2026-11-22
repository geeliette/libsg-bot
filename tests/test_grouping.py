import json
from pathlib import Path

import pytest

from libsg.grouping import suggest_works
from libsg.models import parse_search

FIXTURES = Path(__file__).parent / "fixtures"


def _work_with(works, brn):
    return next(w for w in works if brn in w.brns)


def test_follows_nlb_groups(emma_search):
    groups = parse_search(emma_search)
    works = suggest_works(groups)
    assert [w.brns for w in works] == [tuple(e.brn for e in g.editions) for g in groups if g.editions]


def test_other_language_edition_stays_apart(emma_search):
    works = suggest_works(parse_search(emma_search), physical_only=True)
    english = _work_with(works, 300172872)
    assert set(english.brns) == {300172872, 300123287}
    indonesian = _work_with(works, 4171279)  # 1981 Gramedia: NLB keeps it in its own group
    assert indonesian is not english


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("search_titles_*.json")), ids=lambda p: p.stem)
def test_each_work_is_one_language(path):
    for w in suggest_works(parse_search(json.loads(path.read_text())), physical_only=True):
        assert len({e.language for e in w.editions}) == 1, w.title


def test_order_follows_nlb_relevance(emma_search):
    works = suggest_works(parse_search(emma_search))
    assert works[0].title == "Emma"


def test_physical_only(emma_search):
    works = suggest_works(parse_search(emma_search), physical_only=True)
    assert all(e.physical for w in works for e in w.editions)
    # A group with only digital editions disappears instead of showing up empty.
    assert all(w.editions for w in works)
    all_works = suggest_works(parse_search(emma_search))
    assert len(works) < len(all_works)
