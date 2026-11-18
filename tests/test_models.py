from libsg.models import (
    Edition,
    available_by_branch,
    clean_title,
    parse_copies,
    parse_search,
    parse_titles,
    parse_year,
)


def test_clean_title_and_year():
    assert clean_title("Emma / Jane Austen ; edited by X.") == "Emma"
    assert clean_title("Emma.") == "Emma"
    assert clean_title(None) == "?"
    assert parse_year("2012.") == 2012
    assert parse_year("c1981") == 1981
    assert parse_year("") is None


def test_parse_search_groups(emma_search):
    groups = parse_search(emma_search)
    assert len(groups) == len(emma_search["titles"])
    emma = groups[0]
    assert emma.title == "Emma"
    assert emma.author.startswith("Austen, Jane")
    assert len(emma.editions) == 10
    # Editions inherit the group's title/author.
    assert all(e.title == "Emma" for e in emma.editions)


def test_physical_vs_digital(emma_search):
    eds = {e.brn: e for g in parse_search(emma_search) for e in g.editions}
    assert eds[300172872].physical and eds[300172872].format_name == "Book"
    assert not eds[203603620].physical  # Overdrive ebook
    assert not eds[203378333].physical  # Overdrive e-audiobook
    assert sum(e.physical for e in eds.values()) == 28


def test_parse_titles(austen_titles):
    eds = parse_titles(austen_titles)
    assert len(eds) == 5
    assert all(isinstance(e, Edition) for e in eds)
    assert eds[0].brn == 12415010


def test_copies_statuses(emma_avail):
    copies = parse_copies(emma_avail)
    assert len(copies) == 24
    assert sum(c.available for c in copies) == 4
    assert sum(c.status_code == "Out" for c in copies) == 19
    in_transit = [c for c in copies if c.status_code == "In-Transit"]
    assert len(in_transit) == 1 and not in_transit[0].available
    assert all(c.due_date for c in copies if c.status_code == "Out")
    assert all(c.due_date is None for c in copies if c.status_code != "Out")


def test_reference_only_is_not_available(reference_avail):
    (copy,) = parse_copies(reference_avail)
    assert copy.status_code == "In"
    assert copy.reference_only
    assert not copy.available


def test_available_by_branch(emma_avail):
    counts = available_by_branch(parse_copies(emma_avail))
    assert len(counts) == 20
    assert counts["TRL"] == 1  # Tampines: 1 on shelf, 1 on loan
    assert counts["TPPL"] == 0  # Toa Payoh: on loan
    assert counts["CLL"] == 0  # Central: 2 on loan
    assert sum(counts.values()) == 4
