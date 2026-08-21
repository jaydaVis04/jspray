from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from jayspray.identity import normalized_model
from jayspray.models import FirmwareObservation
from jayspray.sources.base import FirmwareSource, ParserError, SourcePage
from jayspray.sources.http import HttpClient

BASE_URL = "https://samfrew.com"
ALLOWED_HOSTS = frozenset({"samfrew.com", "www.samfrew.com"})
DOWNLOAD_RE = re.compile(
    r"^/download/(?P<device>[^/]+)/(?P<record>[^/]+)/(?P<csc>[A-Z0-9]{3,4})/"
    r"(?P<pda>[A-Z0-9._+-]{4,160})/(?P<csc_version>[A-Z0-9._+-]{4,160})/?$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


class _SamFrewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.rows: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self.current_href is not None:
            return
        href = dict(attrs).get("href") or ""
        if DOWNLOAD_RE.fullmatch(href):
            self.current_href = href
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href is not None:
            self.rows.append((self.current_href, self.current_text.copy()))
            self.current_href = None
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is None:
            return
        value = " ".join(data.split())
        if value:
            self.current_text.append(value)


def parse_samfrew(html: str) -> tuple[FirmwareObservation, ...]:
    parser = _SamFrewParser()
    parser.feed(html)
    observations: list[FirmwareObservation] = []
    seen: set[str] = set()
    for href, text in parser.rows:
        match = DOWNLOAD_RE.fullmatch(href)
        if match is None:
            continue
        model = next((item for item in text if item.upper().startswith("SM-")), None)
        if model is None:
            continue
        try:
            model = normalized_model(model)
        except ValueError:
            continue
        record_key = match.group("record").lower()
        if record_key in seen:
            continue
        seen.add(record_key)
        pda = match.group("pda").upper()
        csc = match.group("csc").upper()
        date = next((item for item in text if DATE_RE.fullmatch(item)), None)
        android = None
        for index, item in enumerate(text[:-1]):
            if item == "Android" and text[index + 1].isdigit():
                android = text[index + 1]
                break
        changelist = next(
            (item for item in reversed(text) if item.isdigit() and len(item) >= 6), None
        )
        device_name = unquote(match.group("device").replace("__", " ").replace("_", " "))
        observations.append(
            FirmwareObservation(
                source="samfrew",
                source_record_key=record_key,
                source_url=f"{BASE_URL}/firmware",
                detail_url=urljoin(BASE_URL, href),
                model=model,
                sales_csc=csc,
                device_name=device_name or None,
                ap_version=pda,
                csc_version=match.group("csc_version").upper(),
                android_version=android,
                changelist=changelist,
                build_date=date,
                source_upload_date=date,
                download_status="indexed",
            )
        )
    if not observations:
        raise ParserError("SamFrew page contained no recognizable firmware rows")
    return tuple(observations)


class SamFrewSource(FirmwareSource):
    name = "samfrew"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_page(self, page: int = 0) -> SourcePage:
        if page < 0:
            raise ValueError("page must not be negative")
        offset = page * 10
        path = "/firmware" if page == 0 else f"/firmware/upload/Desc/{offset}/10"
        response = self.client.get_text(urljoin(BASE_URL, path), allowed_hosts=ALLOWED_HOSTS)
        return SourcePage(parse_samfrew(response.body), next_page=page + 1)
