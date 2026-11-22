from libsg.branches import parse_branches


def test_only_lending_libraries(branches_data):
    codes = {b.code for b in parse_branches(branches_data)}
    assert "LKCRL" not in codes  # National Library reference
    assert "NAS" not in codes and "SBGL" not in codes  # "Other" types
    assert {"TRL", "TPPL", "CNPL", "CAL"} <= codes


def test_open_vs_closed(branches_data):
    bs = {b.code: b for b in parse_branches(branches_data)}
    for code in ("AMKPL", "CSPL", "MPPL", "OCPL", "QUPL"):
        assert not bs[code].is_open, code
        assert bs[code].note  # closure remark kept for display
    assert "renovations" in bs["QUPL"].note
    assert "<" not in bs["OCPL"].note  # HTML stripped
    assert bs["CNPL"].is_open and bs["TRL"].is_open
    assert sum(b.is_open for b in bs.values()) == 22


def test_names(branches_data):
    bs = {b.code: b for b in parse_branches(branches_data)}
    assert bs["TPPL"].short_name == "Toa Payoh"
    assert bs["CAL"].short_name == "Central Arts"
    assert bs["CNPL"].name == "Chinatown Library"


def test_missing_or_empty_response():
    assert parse_branches({}) == []
    assert parse_branches({"branches": None}) == []


# --- opening hours ------------------------------------------------------------

from datetime import datetime

from libsg.branches import SINGAPORE, any_open, parse_hours


def sg(*args):
    return datetime(*args, tzinfo=SINGAPORE)


def test_parse_hours(branches_data):
    hours = parse_hours(branches_data)
    assert "AMKPL" not in hours and "QUPL" not in hours  # closed for relocation / renovation
    bukit_batok = hours["BBPL"]  # 11:00-21:00 every day
    monday = sg(2026, 11, 23, 12, 0)
    assert bukit_batok.is_open(monday)
    assert not bukit_batok.is_open(monday.replace(hour=10, minute=30))
    assert not bukit_batok.is_open(monday.replace(hour=21, minute=0))  # closing time itself is closed
    assert not bukit_batok.is_open(sg(2026, 12, 25, 12, 0))  # Christmas Day: public holiday


def test_any_open_window(branches_data):
    hours = parse_hours(branches_data)
    monday = sg(2026, 11, 23)
    assert not any_open(hours, monday.replace(hour=9, minute=59))
    assert any_open(hours, monday.replace(hour=10, minute=0))  # Bedok opens at 10
    assert any_open(hours, monday.replace(hour=21, minute=29))  # 30 min grace after closing
    assert not any_open(hours, monday.replace(hour=21, minute=30))
    assert not any_open(hours, sg(2026, 12, 25, 15, 0))  # every library closed on Christmas
    # Works from any timezone: 02:00 UTC = 10:00 in Singapore.
    from datetime import timezone
    assert any_open(hours, datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc))


def test_parse_hours_handles_missing_data():
    assert parse_hours({}) == {}
    assert parse_hours({"branches": [{"branchCode": "X", "branchType": {"code": "PL"}, "schedules": [
        {"day": "Funday", "openingTime": "bad", "closingTime": "21:00:00"}]}]}) == {}
