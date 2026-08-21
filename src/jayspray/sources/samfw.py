from __future__ import annotations

from jayspray.sources.base import FirmwareSource, SourceError, SourcePage
from jayspray.sources.http import HttpClient


class SamFWSource(FirmwareSource):
    """Explicit placeholder for a source that currently blocks ordinary HTTP access.

    JAYSPRAY does not bypass SamFW's access controls. The adapter is retained so a
    future legitimate public feed can be added without changing orchestration.
    """

    name = "samfw"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_page(self, page: int = 0) -> SourcePage:
        raise SourceError(
            "SamFW does not currently expose its latest feed to ordinary HTTP requests; "
            "JAYSPRAY will not bypass that restriction"
        )
