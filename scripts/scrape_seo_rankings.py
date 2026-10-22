#!/usr/bin/env python3
"""Capture public-page provenance for owned SEO domains.

This is not a SERP ranking collector. Paid search APIs (Firecrawl, Google CSE)
are out of the default path. The scheduler job still uses the historical name
``seo_rankings`` so coverage stays aligned with jobs.yaml.

Durable SEO lake/API ownership remains in ``book-seo-data``.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "httpx and beautifulsoup4 are required for SEO page capture"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "exported"
DEFAULT_DOMAINS = ["bookchaowalit.com", "chaowalit.com"]
DEFAULT_KEYWORDS = [
    "next.js developer bangkok",
    "python developer thailand",
    "full-stack developer bangkok",
    "AI developer thailand",
    "web scraping service",
    "chatbot developer bangkok",
    "freelance developer thailand",
    "react developer bangkok",
]
MAX_DOMAINS = 10
MAX_KEYWORDS = 20
DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)
SNAPSHOT_FIELDS = [
    "captured_at",
    "domain",
    "url",
    "http_status",
    "title",
    "canonical",
    "content_length",
    "data_status",
    "rank",
    "keyword",
    "source",
]
HISTORY_FIELDS = [
    "captured_at",
    "domain",
    "url",
    "http_status",
    "title",
    "data_status",
    "source",
]


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _as_values(values: Iterable[str] | str | None, default: list[str]) -> list[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in result:
            result.append(item)
    return result


def normalize_domain(value: str) -> str:
    raw = str(value).strip().lower()
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    raw = raw.split("/")[0].split(":")[0].removeprefix("www.")
    if not DOMAIN_RE.fullmatch(raw):
        raise ValueError(f"invalid SEO domain: {value!r}")
    return raw


def normalize_request(
    domains: Iterable[str] | str | None,
    keywords: Iterable[str] | str | None,
) -> tuple[list[str], list[str]]:
    domain_list: list[str] = []
    for item in _as_values(domains, DEFAULT_DOMAINS):
        domain = normalize_domain(item)
        if domain not in domain_list:
            domain_list.append(domain)
    keyword_list = _as_values(keywords, DEFAULT_KEYWORDS)
    if not domain_list or len(domain_list) > MAX_DOMAINS:
        raise ValueError(f"domains must contain 1 to {MAX_DOMAINS} values")
    if not keyword_list or len(keyword_list) > MAX_KEYWORDS:
        raise ValueError(f"keywords must contain 1 to {MAX_KEYWORDS} values")
    return domain_list, keyword_list


def page_url(domain: str) -> str:
    return urlunsplit(("https", domain, "/", "", ""))


# Zero-width space, word joiner, BOM and soft hyphen all render as
# nothing, so a title made only of them must count as blank.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u2060\ufeff\u00ad"))


def _clean_text(value: Any, limit: int) -> str:
    # BeautifulSoup's get_text() has already decoded entities; unescaping again
    # turned a literal "&amp;lt;" in the page into "<".
    text = str(value or "").translate(_INVISIBLE)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_page(domain: str, url: str, status_code: int, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    title = _clean_text(soup.title.get_text() if soup.title else "", 200)
    canonical = ""
    # Link types are ASCII case-insensitive (rel="Canonical" counts).
    link = soup.find(
        "link",
        rel=lambda value: bool(value) and "canonical" in str(value).lower().split(),
    )
    if link and link.get("href"):
        canonical = str(link["href"]).strip()
    ok = 200 <= int(status_code) < 400 and bool(title)
    return {
        "domain": domain,
        "url": url,
        "http_status": int(status_code),
        "title": title,
        "canonical": canonical,
        "content_length": len(html or ""),
        "data_status": "ok" if ok else "error",
        "rank": "",
        "keyword": "",
        "source": "public_page_check",
    }


def fetch_page(domain: str) -> tuple[bytes, dict[str, Any]]:
    url = page_url(domain)
    try:
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        with httpx.Client(
            transport=transport, follow_redirects=True, timeout=30
        ) as client:
            response = client.get(
                url,
                headers={
                    "User-Agent": "book-job-scraping/1.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
    except httpx.HTTPError as exc:
        row = parse_page(domain, url, 0, "")
        row["title"] = str(exc)[:200]
        return b"", row
    html = response.text
    row = parse_page(domain, str(response.url), response.status_code, html)
    raw = getattr(response, "content", b"") or html.encode("utf-8", errors="replace")
    return raw, row


def write_raw(payload: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "seo_rankings_raw.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_snapshot(
    rows: list[dict[str, Any]], captured_at: str, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "seo_rankings.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=SNAPSHOT_FIELDS, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "captured_at": captured_at})
    return path


def append_history(
    rows: list[dict[str, Any]], captured_at: str, output_dir: Path
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "seo_rankings_history.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=HISTORY_FIELDS, extrasaction="ignore"
        )
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({**row, "captured_at": captured_at})
    return path


class SEORankingScraper:
    """Scheduler adapter for bounded public-page SEO provenance."""

    def __init__(
        self,
        keywords: Iterable[str] | str | None = None,
        domains: Iterable[str] | str | None = None,
        alert_improve: int = 5,
        output_dir: str | Path | None = None,
        **_: Any,
    ) -> None:
        self.domains, self.keywords = normalize_request(domains, keywords)
        if (
            isinstance(alert_improve, bool)
            or not isinstance(alert_improve, int)
            or alert_improve < 0
        ):
            raise ValueError("alert_improve must be a non-negative integer")
        self.alert_improve = alert_improve
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR

    async def run(self, **_: Any) -> list[dict[str, Any]]:
        captured_at = _utc_now()
        rows: list[dict[str, Any]] = []
        raw_pages: dict[str, int] = {}
        for domain in self.domains:
            raw, row = fetch_page(domain)
            raw_pages[domain] = len(raw)
            rows.append(row)
        ok_rows = [row for row in rows if row["data_status"] == "ok"]
        if not ok_rows:
            raise ValueError(
                "SEO public-page capture returned no reachable owned domains"
            )
        raw_path = write_raw(
            {
                "source": "public_page_check",
                "keywords_requested": self.keywords,
                "rank_collected": False,
                "pages": raw_pages,
                "rows": rows,
            },
            self.output_dir,
        )
        snapshot_path = write_snapshot(rows, captured_at, self.output_dir)
        history_path = append_history(rows, captured_at, self.output_dir)
        print(f"[seo_rankings] {len(ok_rows)} reachable pages -> {snapshot_path}")
        return [
            {
                "source": "seo_rankings",
                "count": len(ok_rows),
                "output": str(snapshot_path),
                "history": str(history_path),
                "raw": str(raw_path),
                "failed_domains": [
                    row["domain"] for row in rows if row["data_status"] != "ok"
                ],
            }
        ]


if __name__ == "__main__":
    import asyncio

    asyncio.run(SEORankingScraper().run())
