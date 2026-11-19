"""NLB lending libraries a user can watch.

Codes match GetAvailabilityInfo `location.code`. The spec lists codes (in the
LocationCode pattern) but not names ("table T001" isn't included), so names
come from two places:

- VERIFIED: seen as location code/name pairs in real API responses
  (tests/fixtures/). The SearchTitles location facet is capped at 24 values,
  so it doesn't list everything.
- UNVERIFIED: public NLB branch names for codes in the spec that haven't
  appeared in our data yet. Replace with the API's name if it differs.

Reference-only and back-office locations (Lee Kong Chian Reference Library
levels, Repository Used Book Collection, PublicationSG, mobile library) are
excluded: nothing there can be borrowed.
"""

from __future__ import annotations

VERIFIED: dict[str, str] = {
    "BEPL": "Bedok Library",
    "BIPL": "Bishan Library",
    "BBPL": "Bukit Batok Library",
    "BPPL": "Bukit Panjang Library",
    "CAL": "Central Arts Library",
    "CLL": "Central Library",
    "CCKPL": "Choa Chu Kang Library",
    "CMPL": "Clementi Library",
    "GEPL": "Geylang East Library",
    "HBPL": "Harbourfront Library",
    "JRL": "Jurong Library",
    "JWPL": "Jurong West Library",
    "PRPL": "Pasir Ris Library",
    "PRL": "Punggol Library",
    "SBPL": "Sembawang Library",
    "SKPL": "Sengkang Library",
    "SRPL": "Serangoon Library",
    "TRL": "Tampines Library",
    "TPPL": "Toa Payoh Library",
    "WRL": "Woodlands Library",
    "YIPL": "Yishun Library",
}

UNVERIFIED: dict[str, str] = {
    "AMKPL": "Ang Mo Kio Library",
    "BMPL": "Bukit Merah Library",
    "CSPL": "Cheng San Library",
    "CNPL": "library@chinatown",
    "EPPL": "library@esplanade",
    "MPPL": "Marine Parade Library",
    "OCPL": "library@orchard",
    "QUPL": "Queenstown Library",
}

BRANCHES: dict[str, str] = dict(sorted({**VERIFIED, **UNVERIFIED}.items(), key=lambda kv: kv[1].lower()))


def branch_name(code: str, fallback: str | None = None) -> str:
    return BRANCHES.get(code.upper()) or fallback or code


def short_name(code: str) -> str:
    """Button-sized label: "Toa Payoh Library" -> "Toa Payoh"."""
    name = branch_name(code)
    return name.removesuffix(" Library") if name.endswith(" Library") and name != "Central Library" else name
