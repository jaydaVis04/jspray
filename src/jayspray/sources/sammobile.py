from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from jayspray.models import FirmwareObservation
from jayspray.sources.base import FirmwareSource, ParserError, SourcePage
from jayspray.sources.http import HttpClient

BASE_URL = "https://www.sammobile.com"
ALLOWED_HOSTS = frozenset({"www.sammobile.com", "sammobile.com"})
DETAIL_RE = re.compile(
    r"^/samsung/(?:[^/]+/)?firmware/(?P<model>SM-[A-Z0-9]+)/(?P<csc>[A-Z0-9]{3,4})/"
    r"download/(?P<pda>[A-Z0-9._+-]+)/(?P<record>\d+)/?$",
    re.IGNORECASE,
)


class _SamMobileParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.cell_text: list[str] = []
        self.href: str | None = None
        self.rows: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_row = True
            self.cells = []
            self.href = None
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.cell_text = []
        elif tag == "a" and self.in_row:
            href = dict(attrs).get("href")
            if href and DETAIL_RE.fullmatch(urlsplit(href).path):
                self.href = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            self.cells.append(" ".join(self.cell_text).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.href and len(self.cells) >= 5:
                self.rows.append((self.href, self.cells.copy()))
            self.in_row = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            value = " ".join(data.split())
            if value:
                self.cell_text.append(value)


def parse_sammobile(html: str) -> tuple[FirmwareObservation, ...]:
    parser = _SamMobileParser()
    parser.feed(html)
    observations: list[FirmwareObservation] = []
    for href, cells in parser.rows:
        match = DETAIL_RE.fullmatch(urlsplit(href).path)
        if match is None:
            continue
        observations.append(
            FirmwareObservation(
                source="sammobile",
                source_record_key=match.group("record"),
                source_url=f"{BASE_URL}/firmwares/",
                detail_url=urljoin(BASE_URL, href),
                model=match.group("model"),
                sales_csc=match.group("csc"),
                country=cells[1] or None,
                ap_version=match.group("pda"),
                android_version=cells[3] or None,
                build_date=cells[2] or None,
                source_upload_date=cells[2] or None,
                download_status="indexed",
            )
        )
    if not observations:
        raise ParserError("SamMobile page contained no recognizable latest-firmware rows")
    return tuple(observations)


class SamMobileSource(FirmwareSource):
    name = "sammobile"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_page(self, page: int = 0) -> SourcePage:
        if page != 0:
            return SourcePage(())
        response = self.client.get_text(f"{BASE_URL}/firmwares/", allowed_hosts=ALLOWED_HOSTS)
        return SourcePage(parse_sammobile(response.body))
