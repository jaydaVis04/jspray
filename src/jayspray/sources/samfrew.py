from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from jayspray.identity import normalized_model
from jayspray.models import TargetObservation
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


def parse_samfrew(html: str) -> tuple[TargetObservation, ...]:
    parser = _SamFrewParser()
    parser.feed(html)
    observations: list[TargetObservation] = []
    seen: set[tuple[str, str]] = set()
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
        csc = match.group("csc").upper()
        target_key = (model, csc)
        if target_key in seen:
            continue
        seen.add(target_key)
        device_name = unquote(match.group("device").replace("__", " ").replace("_", " "))
        date = next((item for item in text if DATE_RE.fullmatch(item)), None)
        observations.append(
            TargetObservation(
                source="samfrew",
                source_record_key=f"{model}:{csc}",
                source_url=f"{BASE_URL}/firmware",
                detail_url=urljoin(BASE_URL, href),
                model=model,
                sales_csc=csc,
                device_name=device_name or None,
                source_updated_date=date,
                extra={"index_record": match.group("record").lower()},
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
