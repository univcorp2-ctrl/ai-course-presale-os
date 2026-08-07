from __future__ import annotations

import calendar
import hashlib
import html
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from courseforge.config import Settings, load_yaml
from courseforge.models import SourceDocument


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.minimum_interval = 1.0 / requests_per_second
        self.last_request = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self.last_request
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        self.last_request = time.monotonic()


class NotionSource:
    def __init__(self, settings: Settings) -> None:
        if not settings.notion_token or not settings.notion_data_source_id:
            raise ValueError("Notion token and data source ID are required")
        self.settings = settings
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {settings.notion_token}",
            "Notion-Version": settings.notion_version,
            "Content-Type": "application/json",
        }
        self.rate_limiter = RateLimiter(3.0)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        delays = [1.0, 2.0, 4.0]
        with httpx.Client(timeout=30.0) as client:
            for attempt, delay in enumerate(delays, start=1):
                self.rate_limiter.wait()
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    **kwargs,
                )
                if response.status_code != 429:
                    response.raise_for_status()
                    return response
                if attempt == len(delays):
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else delay)
        raise RuntimeError("Notion request exhausted retries")

    @staticmethod
    def _rich_text(items: list[dict[str, Any]]) -> str:
        return "".join(str(item.get("plain_text", "")) for item in items)

    def _properties(self, page: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in page.get("properties", {}).items():
            value_type = value.get("type")
            payload = value.get(value_type)
            if value_type in {"title", "rich_text"}:
                result[name] = self._rich_text(payload or [])
            elif value_type in {"select", "status"}:
                result[name] = str((payload or {}).get("name", ""))
            elif value_type == "multi_select":
                result[name] = ", ".join(item.get("name", "") for item in payload or [])
            elif value_type in {"url", "email", "phone_number", "number"}:
                result[name] = "" if payload is None else str(payload)
            elif value_type == "checkbox":
                result[name] = "true" if payload else "false"
        return result

    def _block_text(self, block: dict[str, Any], depth: int = 0) -> str:
        block_type = block.get("type", "")
        payload = block.get(block_type, {})
        text = self._rich_text(payload.get("rich_text", []))
        if block_type == "child_page":
            text = payload.get("title", "")
        lines = [text] if text else []
        if block.get("has_children") and depth < 2:
            cursor: str | None = None
            while True:
                params = {"page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                response = self._request(
                    "GET", f"/blocks/{block['id']}/children", params=params
                ).json()
                for child in response.get("results", []):
                    child_text = self._block_text(child, depth + 1)
                    if child_text:
                        lines.append(child_text)
                if not response.get("has_more"):
                    break
                cursor = response.get("next_cursor")
        return "\n".join(lines)

    def _page_body(self, page_id: str) -> str:
        lines: list[str] = []
        cursor: str | None = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._request(
                "GET", f"/blocks/{page_id}/children", params=params
            ).json()
            for block in payload.get("results", []):
                text = self._block_text(block)
                if text:
                    lines.append(text)
            if not payload.get("has_more"):
                break
            cursor = payload.get("next_cursor")
        return "\n\n".join(lines)

    def collect(self, limit: int = 50) -> list[SourceDocument]:
        statuses = {
            item.strip()
            for item in self.settings.notion_allowed_statuses.split(",")
            if item.strip()
        }
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(pages) < limit:
            body: dict[str, Any] = {
                "page_size": min(100, limit - len(pages)),
                "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            }
            if cursor:
                body["start_cursor"] = cursor
            response = self._request(
                "POST",
                f"/data_sources/{self.settings.notion_data_source_id}/query",
                json=body,
            ).json()
            pages.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        documents: list[SourceDocument] = []
        for page in pages:
            properties = self._properties(page)
            status = properties.get(self.settings.notion_status_property, "")
            if statuses and status not in statuses:
                continue
            title = next((value for key, value in properties.items() if key.casefold() in {"title", "name", "名前", "タイトル"} and value), "Untitled")
            body = self._page_body(page["id"])
            if not body.strip():
                continue
            documents.append(
                SourceDocument(
                    id=f"notion:{page['id']}",
                    source_type="notion",
                    title=title,
                    text=body,
                    url=page.get("url"),
                    published_at=page.get("last_edited_time"),
                    metadata={"properties": properties, "status": status},
                )
            )
        return documents


class RSSSource:
    def __init__(
        self,
        *,
        name: str,
        url: str,
        allowed_domains: set[str],
        max_age_days: int,
        max_items: int,
    ) -> None:
        self.name = name
        self.url = url
        self.allowed_domains = allowed_domains
        self.max_age_days = max_age_days
        self.max_items = max_items

    @staticmethod
    def _clean_html(value: str) -> str:
        no_tags = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()

    def collect(self) -> list[SourceDocument]:
        feed = feedparser.parse(self.url)
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        documents: list[SourceDocument] = []
        for entry in feed.entries[: self.max_items]:
            link = str(entry.get("link", ""))
            domain = urlparse(link).hostname or ""
            if self.allowed_domains and not any(
                domain == allowed or domain.endswith(f".{allowed}")
                for allowed in self.allowed_domains
            ):
                continue
            published_at = None
            struct_time = entry.get("published_parsed") or entry.get("updated_parsed")
            if struct_time:
                published_at = datetime.fromtimestamp(
                    calendar.timegm(struct_time), tz=timezone.utc
                )
                if published_at < cutoff:
                    continue
            summary = self._clean_html(
                str(entry.get("summary") or entry.get("description") or "")
            )
            if not summary:
                continue
            digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]
            documents.append(
                SourceDocument(
                    id=f"rss:{digest}",
                    source_type="rss",
                    title=str(entry.get("title", "Untitled")),
                    text=summary,
                    url=link,
                    author=str(entry.get("author", "")) or None,
                    published_at=published_at,
                    metadata={"feed": self.name},
                )
            )
        return documents


class LocalMarkdownSource:
    def __init__(self, paths: list[Path]) -> None:
        self.paths = paths

    def collect(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        for path in self.paths:
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            documents.append(
                SourceDocument(
                    id=f"local:{path.as_posix()}",
                    source_type="local",
                    title=path.stem.replace("-", " "),
                    text=text,
                    metadata={"path": path.as_posix()},
                )
            )
        return documents


def deduplicate_documents(documents: list[SourceDocument]) -> list[SourceDocument]:
    seen: set[str] = set()
    result: list[SourceDocument] = []
    for document in documents:
        fingerprint = document.fingerprint()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(document)
    return result


def collect_sources(settings: Settings) -> tuple[list[SourceDocument], list[str]]:
    config = load_yaml(settings.source_config_path)
    warnings: list[str] = []
    documents: list[SourceDocument] = []

    local_paths = [Path(item) for item in config.get("local_markdown", [])]
    documents.extend(LocalMarkdownSource(local_paths).collect())

    if settings.notion_token and settings.notion_data_source_id:
        try:
            documents.extend(NotionSource(settings).collect())
        except Exception as exc:  # Source isolation is intentional.
            warnings.append(f"Notion source failed: {type(exc).__name__}: {exc}")
    else:
        warnings.append("Notion source skipped: credentials or data source ID not configured")

    rss_defaults = config.get("rss_defaults", {})
    allowed_domains = set(config.get("allowed_domains", []))
    for feed in config.get("rss", []):
        if not feed.get("enabled", True):
            continue
        try:
            documents.extend(
                RSSSource(
                    name=feed["name"],
                    url=feed["url"],
                    allowed_domains=allowed_domains,
                    max_age_days=int(feed.get("max_age_days", rss_defaults.get("max_age_days", 21))),
                    max_items=int(feed.get("max_items", rss_defaults.get("max_items", 10))),
                ).collect()
            )
        except Exception as exc:
            warnings.append(f"RSS source {feed.get('name', 'unknown')} failed: {type(exc).__name__}: {exc}")

    return deduplicate_documents(documents), warnings
