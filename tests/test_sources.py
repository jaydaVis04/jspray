from pathlib import Path

import pytest

from jayspray.sources.base import ParserError, SourceError
from jayspray.sources.http import _AllowlistedRedirectHandler
from jayspray.sources.samfrew import parse_samfrew
from jayspray.sources.sammobile import parse_sammobile

FIXTURES = Path(__file__).parent / "fixtures"


def test_samfrew_latest_parser() -> None:
    rows = parse_samfrew((FIXTURES / "samfrew/latest.html").read_text(encoding="utf-8"))
    assert [(item.model, item.sales_csc) for item in rows] == [
        ("SM-S928U1", "XAA"),
        ("SM-S928U1", "CCT"),
    ]
    assert rows[0].source_updated_date == "7/6/2026"


def test_sammobile_latest_parser() -> None:
    rows = parse_sammobile((FIXTURES / "sammobile/latest.html").read_text(encoding="utf-8"))
    assert [(item.model, item.sales_csc) for item in rows] == [
        ("SM-F971U", "XAA"),
        ("SM-F971Q", "SJP"),
    ]
    assert rows[0].country == "USA"
    assert rows[0].source_updated_date == "2026-08-21"


@pytest.mark.parametrize("parser", [parse_samfrew, parse_sammobile])
def test_empty_page_is_explicit_parser_failure(parser: object) -> None:
    with pytest.raises(ParserError):
        parser("<html><body>No current rows</body></html>")  # type: ignore[operator]


def test_discovery_redirect_cannot_leave_allowlisted_https_origin() -> None:
    handler = _AllowlistedRedirectHandler(frozenset({"samfrew.com"}))
    with pytest.raises(SourceError):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/private")
