from __future__ import annotations

import random
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlsplit

from jayspray.config import HttpConfig
from jayspray.sources.base import SourceError

_RNG = random.SystemRandom()


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    body: str
    etag: str | None
    last_modified: str | None


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise SourceError("refusing a discovery redirect outside its fixed HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    """Small fixed-origin client with normal TLS validation and bounded responses."""

    def __init__(self, config: HttpConfig) -> None:
        self.config = config
        self._ssl_context = ssl.create_default_context()
        self._rate_lock = Lock()
        self._next_request_at: dict[str, float] = {}

    def _wait_for_rate_limit(self, host: str) -> None:
        now = time.monotonic()
        with self._rate_lock:
            slot = max(now, self._next_request_at.get(host, now))
            self._next_request_at[host] = slot + self.config.request_delay_seconds
        remaining = slot - now
        if remaining > 0:
            time.sleep(remaining)

    def get_text(self, url: str, *, allowed_hosts: frozenset[str]) -> HttpResponse:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise SourceError("refusing a discovery URL outside its fixed HTTPS origin")
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": self.config.user_agent,
        }
        request = urllib.request.Request(  # noqa: S310 - validated fixed HTTPS origin
            url, headers=headers, method="GET"
        )
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context),
            _AllowlistedRedirectHandler(allowed_hosts),
        )
        assert parsed.hostname is not None
        self._wait_for_rate_limit(parsed.hostname)
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            if attempt:
                delay = self.config.retry_base_seconds * (2 ** (attempt - 1))
                time.sleep(delay + _RNG.uniform(0.0, min(delay, 1.0)))
            try:
                with opener.open(request, timeout=self.config.timeout_seconds) as response:
                    content_type = response.headers.get_content_type()
                    if content_type not in {"text/html", "application/xhtml+xml"}:
                        raise SourceError(f"unexpected discovery content type: {content_type}")
                    raw = response.read(self.config.max_response_bytes + 1)
                    if len(raw) > self.config.max_response_bytes:
                        raise SourceError("discovery response exceeded the configured size limit")
                    charset = response.headers.get_content_charset() or "utf-8"
                    return HttpResponse(
                        url=response.geturl(),
                        body=raw.decode(charset, errors="replace"),
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                    )
            except (urllib.error.URLError, TimeoutError, SourceError) as exc:
                last_error = exc
        assert last_error is not None
        raise SourceError(f"HTTP discovery failed after retries: {last_error}") from last_error
