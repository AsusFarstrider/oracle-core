from __future__ import annotations

import email.utils
from html.parser import HTMLParser
import ipaddress
import re
import socket
from datetime import datetime
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit
from xml.etree import ElementTree

from oracle_app.configuration.domain_models import NewsSource, RssNewsProvider


class NewsBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class NewsBridgeConfigurationError(NewsBridgeError):
    pass


class _ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._article_depth = 0
        self._paragraph_depth = 0
        self.article_paragraphs: list[str] = []
        self.fallback_paragraphs: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() == "article":
            self._article_depth += 1
        if tag.casefold() == "p":
            self._paragraph_depth += 1
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "p" and self._paragraph_depth:
            text = re.sub(r"\s+", " ", " ".join(self._parts)).strip()
            if text:
                self.fallback_paragraphs.append(text)
                if self._article_depth:
                    self.article_paragraphs.append(text)
            self._paragraph_depth -= 1
            self._parts = []
        if lowered == "article" and self._article_depth:
            self._article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._paragraph_depth and data.strip():
            self._parts.append(data.strip())


class _ValidatedArticleRedirectHandler(request.HTTPRedirectHandler):
    def __init__(self, validator) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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

    def fetch_typed_article(
        self,
        *,
        article_url: str,
        source: NewsSource,
        provider: RssNewsProvider,
    ) -> dict[str, Any]:
        allowed_hosts = self._article_hosts(source)
        validate = lambda value: self._validate_article_url(value, allowed_hosts=allowed_hosts)
        validate(article_url)
        opener = request.build_opener(_ValidatedArticleRedirectHandler(validate))
        req = request.Request(
            article_url,
            method="GET",
            headers={"User-Agent": "oracle-brain-news/1.0", "Accept": "text/html,application/xhtml+xml"},
        )
        try:
            with opener.open(req, timeout=provider.timeout_seconds) as response:
                final_url = str(response.geturl() or article_url)
                validate(final_url)
                content_type = str(response.headers.get("Content-Type") or "").casefold()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise NewsBridgeError("news_article_unavailable", "Selected story did not return HTML")
                payload = response.read(524289)
        except NewsBridgeError:
            raise
        except error.HTTPError as exc:
            raise NewsBridgeError(
                "news_article_unavailable", f"Selected story returned HTTP {exc.code}"
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            detail = str(getattr(exc, "reason", exc))
            raise NewsBridgeError("news_article_unavailable", detail) from exc
        if len(payload) > 524288:
            raise NewsBridgeError("news_article_unavailable", "Selected story was too large")
        parser = _ArticleTextParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        paragraphs = parser.article_paragraphs or parser.fallback_paragraphs
        excerpt = self._bounded_excerpt(paragraphs)
        if not excerpt:
            raise NewsBridgeError("news_article_unavailable", "Selected story had no readable article text")
        return {"url": final_url, "excerpt": excerpt}

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
            summary = self._clean_markup(item.findtext("description"))
            parsed.append(
                {
                    "title": title,
                    "link": link,
                    "published_at": self._parse_pub_date(pub_date_raw),
                    "published_label": pub_date_raw,
                    "summary": summary,
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

    def _clean_markup(self, value: str | None) -> str:
        parser = _ArticleTextParser()
        parser.feed(f"<p>{str(value or '')}</p>")
        paragraphs = parser.fallback_paragraphs
        return self._bounded_excerpt(paragraphs, limit=600)

    def _article_hosts(self, source: NewsSource) -> set[str]:
        feed_host = (urlsplit(source.feed_url).hostname or "").casefold().rstrip(".")
        return {feed_host, *(host.casefold().rstrip(".") for host in source.article_hosts)}

    def _validate_article_url(self, value: str, *, allowed_hosts: set[str]) -> None:
        parsed = urlsplit(str(value or ""))
        host = (parsed.hostname or "").casefold().rstrip(".")
        try:
            port = parsed.port
        except ValueError as exc:
            raise NewsBridgeError("news_article_blocked", "Selected story URL has an invalid port") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
            or host not in allowed_hosts
        ):
            raise NewsBridgeError("news_article_blocked", "Selected story URL is outside its configured source")
        try:
            addresses = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80))
        except OSError as exc:
            raise NewsBridgeError("news_article_unavailable", "Selected story host could not be resolved") from exc
        for address in addresses:
            candidate = ipaddress.ip_address(address[4][0])
            if not candidate.is_global:
                raise NewsBridgeError("news_article_blocked", "Selected story resolved to a non-public address")

    def _bounded_excerpt(self, paragraphs: list[str], *, limit: int = 2000) -> str:
        collected: list[str] = []
        length = 0
        for paragraph in paragraphs:
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if len(normalized) < 20:
                continue
            remaining = limit - length
            if remaining <= 0:
                break
            collected.append(normalized[:remaining])
            length += len(collected[-1]) + 1
        return " ".join(collected).strip()


def get_news_bridge(source_definition: dict[str, Any], settings: dict[str, Any]) -> RssNewsBridge:
    provider = str(source_definition.get("provider") or settings.get("news_provider") or "rss").strip().lower()
    if provider == "rss":
        return RssNewsBridge()
    raise NewsBridgeConfigurationError("news_fetch_failed", f"Unsupported news provider: {provider}")
