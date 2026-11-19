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
