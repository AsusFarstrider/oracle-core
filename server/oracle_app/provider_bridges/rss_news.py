from __future__ import annotations

import email.utils
import re
from datetime import datetime
from typing import Any
from urllib import error, request
from xml.etree import ElementTree

from oracle_app.configuration.domain_models import NewsSource, RssNewsProvider


class NewsBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class NewsBridgeConfigurationError(NewsBridgeError):
    pass


class RssNewsBridge:
    provider_name = "rss"

    def fetch_headlines(
        self,
        *,
        source_definition: dict[str, Any],
        timeout_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        url = str(source_definition.get("url") or "").strip()
        if not url:
            raise NewsBridgeConfigurationError("news_fetch_failed", "News feed URL is not configured")
        payload = self._fetch_feed(url, timeout_seconds=timeout_seconds)
        headlines = self._parse_rss_items(payload)
        return headlines[: max(1, int(limit))]

    def fetch_typed_headlines(
        self,
        *,
        source: NewsSource,
        provider: RssNewsProvider,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = self._fetch_feed(source.feed_url, timeout_seconds=provider.timeout_seconds)
        headlines = self._parse_rss_items(payload)
        return headlines[: max(1, int(limit))]

    def _fetch_feed(self, url: str, *, timeout_seconds: int) -> str:
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NewsBridgeError("news_fetch_failed", detail or f"News feed returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise NewsBridgeError("news_fetch_failed", str(exc.reason)) from exc

    def _parse_rss_items(self, payload: str) -> list[dict[str, Any]]:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise NewsBridgeError("news_fetch_failed", "News feed returned invalid RSS") from exc
        items = root.findall("./channel/item")
        parsed: list[dict[str, Any]] = []
        for item in items:
            title = self._clean_text(item.findtext("title"))
            link = self._clean_text(item.findtext("link"))
            pub_date_raw = self._clean_text(item.findtext("pubDate"))
            parsed.append(
                {
                    "title": title,
                    "link": link,
                    "published_at": self._parse_pub_date(pub_date_raw),
                    "published_label": pub_date_raw,
                }
            )
        return parsed

    def _parse_pub_date(self, value: str) -> str | None:
        if not value:
            return None
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except Exception:
            return None
        if isinstance(parsed, datetime):
            return parsed.isoformat()
        return None

    def _clean_text(self, value: str | None) -> str:
        text = str(value or "").strip()
        return re.sub(r"\s+", " ", text)


def get_news_bridge(source_definition: dict[str, Any], settings: dict[str, Any]) -> RssNewsBridge:
    provider = str(source_definition.get("provider") or settings.get("news_provider") or "rss").strip().lower()
    if provider == "rss":
        return RssNewsBridge()
    raise NewsBridgeConfigurationError("news_fetch_failed", f"Unsupported news provider: {provider}")
