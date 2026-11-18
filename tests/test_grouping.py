from libsg.grouping import author_key, suggest_works, title_key
from libsg.models import parse_search


def test_keys():
    assert title_key("Emma") == title_key("Emma.") == "emma"
    assert title_key("Emma : The Illustrated Edition") == "emma"
    assert title_key("The annotated emma") == "annotated emma"
    assert author_key("Austen, Jane, 1775-1817") == "austen"
    assert author_key("Sánchez Vegara, Ma Isabel") == "sanchez vegara"


def _work_with(works, brn):
    return next(w for w in works if brn in w.brns)


def test_split_emma_is_merged(emma_search):
    works = suggest_works(parse_search(emma_search))
    emma = _work_with(works, 300172872)
    # NLB put the 1981 hardcopy in its own group; we merge it back.
    assert 4171279 in emma.brns
    assert emma.title == "Emma"
    # The illustrated edition has the same main title + author, so it's suggested too.
    assert 203300531 in emma.brns


def test_different_works_stay_apart(emma_search):
    works = suggest_works(parse_search(emma_search))
    emma = _work_with(works, 300172872)
    for other in (203519325, 203557764, 202670399, 300172891):  # vampires, annotated, Wang, P&P
        assert other not in emma.brns
    # Same title "Jane Austen", different authors.
    assert _work_with(works, 200813179) is not _work_with(works, 201608063)


def test_no_brn_lost_or_duplicated(emma_search):
    groups = parse_search(emma_search)
    all_brns = sorted(e.brn for g in groups for e in g.editions)
    works = suggest_works(groups)
    assert sorted(b for w in works for b in w.brns) == all_brns
    assert len(works) < len(groups)


def test_order_follows_nlb_relevance(emma_search):
    works = suggest_works(parse_search(emma_search))
    assert works[0].title == "Emma"


def test_physical_only(emma_search):
    works = suggest_works(parse_search(emma_search), physical_only=True)
    emma = _work_with(works, 300172872)
    assert set(emma.brns) == {300172872, 300123287, 4171279}
    assert all(e.physical for w in works for e in w.editions)
