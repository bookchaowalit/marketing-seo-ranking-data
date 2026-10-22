#!/usr/bin/env python3
"""
SEO ranking checks (lake-first, public-page only by default).

Each keyword query lands its own provider raw bytes (HTML/JSON) before any
merge. Firecrawl / Google CSE / paid SERP are blocked before network under
free-only policy.

Flow:
    Public page/search response bytes
      → landing/provider=…/query=…
      → Bronze seo_rankings (+ history)
      → DuckDB API
      → optional CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data"

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def _require_httpx():
    try:
        import httpx as _httpx
    except ImportError:
        print("ERROR: httpx required for live ingestion. Install: pip install httpx")
        raise SystemExit(1)
    return _httpx


class _HttpxProxy:
    def __getattr__(self, name):
        return getattr(_require_httpx(), name)


httpx = _HttpxProxy()

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    from . import config as _dp_config
    from .policy import evaluate_provider, require_provider, external_writes_allowed
    from .store import seed_fixtures
    from . import lake as _lake
    from .fsutil import atomic_append_csv, atomic_write_csv
except ImportError:  # pragma: no cover
    _dp_config = None  # type: ignore[assignment]
    _lake = None  # type: ignore[assignment]

    def evaluate_provider(name):  # type: ignore[misc]
        class D:
            allowed = True
            status = "free"
            reason = ""

        return D()

    def require_provider(name):  # type: ignore[misc]
        return evaluate_provider(name)

    def external_writes_allowed():  # type: ignore[misc]
        return False

    def seed_fixtures(data_dir=None):  # type: ignore[misc]
        return None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

DEFAULT_KEYWORDS = [
    "next.js developer bangkok",
    "python developer thailand",
    "full-stack developer bangkok",
    "AI developer thailand",
    "web scraping service",
    "chatbot developer bangkok",
    "freelance developer thailand",
    "react developer bangkok",
    "data analyst thailand",
    "AI integration service",
]

DEFAULT_DOMAINS = [
    "bookchaowalit.com",
    "chaowalit.com",
]


def _firecrawl_key() -> str:
    if _dp_config is not None:
        return getattr(_dp_config, "FIRECRAWL_API_KEY", "") or os.environ.get(
            "FIRECRAWL_API_KEY", ""
        )
    return os.environ.get("FIRECRAWL_API_KEY", "")


def fetch_firecrawl_search_raw(
    query: str, limit: int = 20
) -> tuple[bytes, str, list[dict[str, Any]]]:
    """Paid Firecrawl search — policy-gated before network."""
    decision = evaluate_provider("firecrawl")
    if not decision.allowed:
        print(f"  Firecrawl blocked ({decision.status}): {decision.reason}")
        return b"", "application/json", []
    # Also block google_cse / paid_serp style misuse if someone aliases
    for blocked in ("google_cse", "paid_serp"):
        d = evaluate_provider(blocked)
        if not d.allowed:
            pass  # expected blocked
    api_key = _firecrawl_key()
    if not api_key:
        print("  Firecrawl skipped: missing FIRECRAWL_API_KEY")
        return b"", "application/json", []
    try:
        resp = httpx.post(
            "https://api.firecrawl.dev/v1/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "limit": limit},
            timeout=30,
        )
        raw = getattr(resp, "content", None) or resp.text.encode(
            "utf-8", errors="replace"
        )
        if resp.status_code != 200:
            return raw, "application/json", []
        data = resp.json()
        results = []
        for item in data.get("data", []):
            title = item.get("title", "") or item.get("metadata", {}).get("title", "")
            url = item.get("url", "") or item.get("metadata", {}).get("sourceURL", "")
            desc = item.get("description", "") or item.get("markdown", "")[:200]
            if title and url:
                results.append({"title": title[:200], "url": url, "description": desc})
            if len(results) >= limit:
                break
        return raw, "application/json", results
    except Exception as e:  # noqa: BLE001
        print(f"  Firecrawl fallback failed: {e}")
        return b"", "application/json", []


def fetch_public_page_search_raw(
    query: str, limit: int = 20
) -> tuple[bytes, str, list[dict[str, Any]]]:
    """Direct public search/page collection — free default path."""
    decision = evaluate_provider("public_page_check")
    if not decision.allowed:
        print(f"  Public page check blocked ({decision.status}): {decision.reason}")
        return b"", "text/html", []
    if BeautifulSoup is None:
        print("  Public page check skipped: beautifulsoup4 not installed")
        return b"", "text/html", []
    results: list[dict[str, Any]] = []
    try:
        google_url = (
            f"https://www.google.com/search?q={quote_plus(query)}&num={limit}&hl=en"
        )
        resp = httpx.get(google_url, headers=HEADERS, timeout=15, follow_redirects=True)
        raw = getattr(resp, "content", None) or resp.text.encode(
            "utf-8", errors="replace"
        )
        if resp.status_code != 200:
            # Do not silently call paid fallbacks without policy.
            print(
                f"  Public page HTTP {resp.status_code}; not falling back to paid SERP"
            )
            return raw, "text/html", []
        soup = BeautifulSoup(resp.text, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = str(a_tag["href"])
            m = re.search(r"/url\?q=(https?://[^&]+)", href)
            url = m.group(1) if m else href
            # Skip Google/YouTube hosts only; a substring test also dropped
            # results that merely mention them in their path or query.
            if domain_matches(url, "google.com") or domain_matches(url, "youtube.com"):
                continue
            title = a_tag.get_text(strip=True)
            if title and len(title) > 3:
                results.append({"url": url, "title": title[:200], "description": ""})
            if len(results) >= limit:
                break
        return raw, "text/html", results
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: public page search failed for '{query}': {e}")
        return b"", "text/html", []


def google_search(query: str, limit: int = 20) -> list:
    """Backward-compatible list-only search (drops raw bytes)."""
    _raw, _ctype, results = fetch_public_page_search_raw(query, limit=limit)
    if results:
        return results
    # Paid fallback only if policy allows — still gated inside fetch_firecrawl.
    _raw2, _c2, results2 = fetch_firecrawl_search_raw(query, limit=limit)
    return results2


def normalize_domain(value: str) -> str:
    """``https://www.Example.com/path`` -> ``example.com`` (host only, lowercase)."""
    text = str(value or "").strip().lower()
    if "://" not in text:
        text = "//" + text
    try:
        host = (urlsplit(text).hostname or "").strip(".")
    except ValueError:  # e.g. an unbalanced "[" IPv6 literal
        return ""
    return host[4:] if host.startswith("www.") else host


def domain_matches(url: str, domain: str) -> bool:
    """True when ``url``'s host is ``domain`` or one of its subdomains.

    A plain substring test matched ``notexample.com`` and
    ``example.com.evil.net`` for ``example.com``.
    """
    target = normalize_domain(domain)
    host = normalize_domain(url)
    return bool(target) and (host == target or host.endswith("." + target))


def check_ranking(
    keyword: str,
    target_domains: list,
    limit: int = 20,
    *,
    results: Optional[list] = None,
) -> dict:
    """Check ranking position for a keyword across target domains."""
    if results is None:
        results = google_search(keyword, limit=limit)

    rankings: dict[str, Any] = {
        "keyword": keyword,
        "total_results": len(results),
        "found": False,
        "best_rank": None,
        "best_url": None,
        "best_domain": None,
        "best_title": None,
        "all_positions": [],
    }

    for i, result in enumerate(results, 1):
        url = result.get("url", "")
        title = result.get("title", "")
        for domain in target_domains:
            if domain_matches(url, domain):
                rankings["found"] = True
                rankings["all_positions"].append(
                    {
                        "rank": i,
                        "url": url,
                        "domain": domain,
                        "title": title,
                    }
                )
                if rankings["best_rank"] is None or i < rankings["best_rank"]:
                    rankings["best_rank"] = i
                    rankings["best_url"] = url
                    rankings["best_domain"] = domain
                    rankings["best_title"] = title
    return rankings


def load_previous_rankings(history_file: Path) -> dict:
    previous: dict[str, Any] = {}
    if not history_file.exists():
        return previous
    with open(history_file, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keyword = row.get("keyword", "")
            rank = row.get("best_rank") or row.get("rank")
            if keyword and rank not in (None, ""):
                try:
                    previous[keyword] = int(rank)
                except (ValueError, TypeError):
                    pass
    return previous


def _projection_timestamp() -> str:
    # UTC: readers (product_store.parse_ts / _freshness) treat naive stamps as
    # UTC, so a host-local stamp looked hours fresher (or older) than it was.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def project_rankings_csv(rows: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "seo_rankings.csv"
    fieldnames = [
        "scraped_at",
        "keyword",
        "target_domain",
        "location",
        "device",
        "provider",
        "found",
        "best_rank",
        "rank",
        "best_domain",
        "best_url",
        "result_url",
        "best_title",
        "result_title",
        "observed_at",
        "total_results",
    ]
    scraped = _projection_timestamp()
    atomic_write_csv(
        path,
        fieldnames,
        [{k: r.get(k, "") for k in fieldnames} | {"scraped_at": scraped} for r in rows],
    )
    print(f"  Projected {len(rows)} rankings → {path}")
    return path


def project_history_csv(rows: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "seo_rankings_history.csv"
    fieldnames = [
        "date",
        "keyword",
        "target_domain",
        "best_rank",
        "rank",
        "best_domain",
        "found",
        "provider",
        "observed_at",
    ]
    scraped = _projection_timestamp()
    history_rows = [
        {
            "date": scraped,
            "keyword": r.get("keyword"),
            "target_domain": r.get("target_domain"),
            "best_rank": r.get("rank") or r.get("best_rank") or "",
            "rank": r.get("rank") or "",
            "best_domain": r.get("target_domain") or r.get("best_domain") or "",
            "found": r.get("found"),
            "provider": r.get("provider"),
            "observed_at": r.get("observed_at") or scraped,
        }
        for r in rows
    ]
    atomic_append_csv(path, fieldnames, history_rows)
    print(f"  Projected +{len(rows)} history rows → {path}")
    return path


def save_rankings(rankings: list, output_dir: Path):
    """Backward-compatible CSV snapshot from legacy ranking dicts."""
    rows = []
    for r in rankings:
        rows.append(
            {
                "keyword": r.get("keyword"),
                "target_domain": r.get("best_domain") or "",
                "found": r.get("found"),
                "best_rank": r.get("best_rank") or "",
                "rank": r.get("best_rank") or "",
                "best_domain": r.get("best_domain") or "",
                "best_url": r.get("best_url") or "",
                "result_url": r.get("best_url") or "",
                "best_title": (r.get("best_title") or "")[:100],
                "result_title": (r.get("best_title") or "")[:100],
                "total_results": r.get("total_results"),
            }
        )
    return project_rankings_csv(rows, output_dir)


def append_history(rankings: list, output_dir: Path):
    rows = []
    for r in rankings:
        rows.append(
            {
                "keyword": r.get("keyword"),
                "target_domain": r.get("best_domain") or "",
                "found": r.get("found"),
                "best_rank": r.get("best_rank") or "",
                "rank": r.get("best_rank") or "",
            }
        )
    return project_history_csv(rows, output_dir)


def print_summary(rankings: list, previous: dict, alert_improve: Optional[int] = None):
    found_count = sum(1 for r in rankings if r.get("found"))
    print(f"\n  Rankings: {found_count}/{len(rankings)} keywords found")
    print("\n  Keyword Rankings:")
    for r in rankings:
        keyword = r["keyword"]
        if r.get("found"):
            rank = r.get("best_rank") or r.get("rank")
            prev_rank = previous.get(keyword)
            change = ""
            if prev_rank and rank:
                try:
                    diff = int(prev_rank) - int(rank)
                    if diff > 0:
                        change = f" (+{diff} up)"
                    elif diff < 0:
                        change = f" ({diff} down)"
                    else:
                        change = " (=)"
                except (TypeError, ValueError):
                    pass
            domain = r.get("best_domain") or r.get("target_domain") or ""
            print(f"    #{str(rank):>3} | {keyword:<40} | {domain}{change}")
            if (
                alert_improve
                and prev_rank
                and rank
                and (int(prev_rank) - int(rank)) >= alert_improve
            ):
                print(f"          *** IMPROVED {int(prev_rank) - int(rank)} positions!")
        else:
            print(f"    --- | {keyword:<40} | Not in top 20")


def _land_query(
    *,
    provider: str,
    raw: bytes,
    records: list[dict],
    content_type: str,
    dataset: str,
    data_lake_uri: Optional[str],
    output_dir: Path,
    keyword: str,
) -> dict[str, Any]:
    if _lake is None or _dp_config is None:
        raise RuntimeError("Lake adapter unavailable")
    if not raw:
        raise _lake.LakeIngestError(f"Empty raw payload for keyword {keyword!r}")
    input_format = "html" if content_type.startswith("text/html") else "json"
    result = _lake.ingest_provider_payload(
        provider=provider,
        raw=raw,
        records=records,
        dataset=dataset,
        content_type=content_type,
        input_format=input_format,
        data_lake_uri=data_lake_uri,
        metadata={"keyword": keyword, "query": keyword},
    )
    _lake.write_lineage(
        result,
        dataset=dataset,
        data_dir=output_dir,
        provider=provider,
        query_key=keyword,
    )
    print(
        f"  Lake {dataset}/{provider}: keyword={keyword!r} "
        f"records={result.get('record_count')} raw={result.get('raw_key')}"
    )
    return result


def run_live_ingest(
    *,
    keywords: list[str],
    target_domains: list[str],
    output_dir: Path,
    alert_improve: Optional[int] = None,
    write_history: bool = True,
    data_lake_uri: Optional[str] = None,
    project_csv: bool = True,
    location: Optional[str] = None,
    device: Optional[str] = None,
) -> dict[str, Any]:
    location = location or (_dp_config.DEFAULT_LOCATION if _dp_config else "global")
    device = device or (_dp_config.DEFAULT_DEVICE if _dp_config else "desktop")

    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        "SEO Ranking Checker (lake-first)"
    )
    print(f"  Keywords: {len(keywords)} | Domains: {target_domains}")
    if data_lake_uri:
        print(f"  Data lake: {data_lake_uri}")
    elif _lake is not None:
        print(f"  Data lake: {_lake.default_data_lake_uri()}")

    previous = (
        load_previous_rankings(output_dir / "seo_rankings_history.csv")
        if project_csv
        else {}
    )

    lake_batches: list[dict[str, Any]] = []
    projection_rows: list[dict[str, Any]] = []
    summary_rankings: list[dict[str, Any]] = []
    providers_used: list[str] = []

    for keyword in keywords:
        print(f"  Checking: {keyword}...")
        raw, ctype, results = fetch_public_page_search_raw(keyword, limit=20)
        provider = "public_page_check"
        if not results and not raw:
            # Only then consider paid fallbacks (still policy-gated).
            raw, ctype, results = fetch_firecrawl_search_raw(keyword, limit=20)
            provider = "firecrawl"
        if not raw:
            print(f"    No raw response for {keyword!r} (providers blocked/empty)")
            continue

        ranking = check_ranking(keyword, target_domains, results=results or [])
        summary_rankings.append(ranking)
        records = _lake.ranking_records_from_check(
            ranking,
            provider=provider,
            target_domains=target_domains,
            location=location,
            device=device,
        )
        # Durable write before CSV (fail-closed for this query).
        batch = _land_query(
            provider=provider,
            raw=raw,
            records=records,
            content_type=ctype,
            dataset=_dp_config.LAKE_DATASET_RANKINGS,
            data_lake_uri=data_lake_uri,
            output_dir=output_dir,
            keyword=keyword,
        )
        lake_batches.append(batch)
        providers_used.append(provider)

        if write_history:
            hist_raw = json.dumps(
                {
                    "provider": provider,
                    "dataset": _dp_config.LAKE_DATASET_HISTORY,
                    "parent_run_id": batch.get("run_id"),
                    "keyword": keyword,
                    "records": records,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            hist = _land_query(
                provider=provider,
                raw=hist_raw,
                records=records,
                content_type="application/json",
                dataset=_dp_config.LAKE_DATASET_HISTORY,
                data_lake_uri=data_lake_uri,
                output_dir=output_dir,
                keyword=keyword,
            )
            lake_batches.append(hist)

        for rec in records:
            projection_rows.append(
                {
                    "keyword": rec["keyword"],
                    "target_domain": rec["target_domain"],
                    "location": rec["location"],
                    "device": rec["device"],
                    "provider": rec["provider"],
                    "found": rec["found"],
                    "best_rank": rec["rank"],
                    "rank": rec["rank"],
                    "best_domain": rec["target_domain"],
                    "best_url": rec["result_url"],
                    "result_url": rec["result_url"],
                    "best_title": rec["result_title"],
                    "result_title": rec["result_title"],
                    "observed_at": rec["observed_at"],
                    "total_results": rec.get("total_results", ""),
                }
            )

    if not lake_batches:
        raise RuntimeError(
            "No SEO rankings landed. Ensure public_page_check is allowed "
            "or use --fixture for offline mode."
        )

    if project_csv and projection_rows:
        project_rankings_csv(projection_rows, output_dir)
        if write_history:
            project_history_csv(projection_rows, output_dir)
        print_summary(summary_rankings, previous, alert_improve)

    print(
        f"\n  Landed batches: {len(lake_batches)} | "
        f"Providers: {sorted(set(providers_used))}"
    )
    print("  Done (per-query landing; CSV is projection only).")
    return {
        "keywords": len(keywords),
        "lake_batches": len(lake_batches),
        "providers": sorted(set(providers_used)),
        "projected_csv": project_csv,
    }


def _dedupe_casefold(values) -> list[str]:
    """Drop blanks and case-insensitive repeats, keeping the first spelling."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check SEO rankings (lake-first; public-page default)"
    )
    parser.add_argument("--keywords", default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--domains", default=",".join(DEFAULT_DOMAINS))
    parser.add_argument("--alert-improve", type=int, default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--data-lake-uri", default=None)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-project-csv", action="store_true")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    keywords = (
        _dedupe_casefold(k.strip() for k in args.keywords.split(","))
        if args.keywords is not None
        else list(DEFAULT_KEYWORDS)
    )
    target_domains = list(
        dict.fromkeys(
            normalize_domain(d)
            for d in ([args.domain] if args.domain else args.domains.split(","))
            if normalize_domain(d)
        )
    )
    if not keywords:
        parser.error("--keywords must name at least one keyword")
    if not target_domains:
        parser.error("--domain/--domains must name at least one valid domain")
    if args.alert_improve is not None and args.alert_improve < 1:
        parser.error("--alert-improve must be a positive number of positions")
    output_dir = Path(args.output_dir)

    if args.fixture or args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        if _dp_config is not None:
            _dp_config.DATA_DIR = output_dir
        seed_fixtures(output_dir)
        print(
            "["
            + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            + "] Fixture mode: seeded local projection under "
            + str(output_dir)
        )
        print("  No upstream providers contacted; no lake write.")
        return 0

    try:
        run_live_ingest(
            keywords=keywords,
            target_domains=target_domains,
            output_dir=output_dir,
            alert_improve=args.alert_improve,
            write_history=not args.no_history,
            data_lake_uri=args.data_lake_uri,
            project_csv=not args.no_project_csv,
            location=args.location,
            device=args.device,
        )
    except Exception as exc:  # noqa: BLE001
        if _lake is not None and isinstance(
            exc, (_lake.LakeUnavailable, _lake.LakeIngestError)
        ):
            print(
                f"ERROR: lake ingest failed before projection: {exc}",
                file=sys.stderr,
            )
            return 2
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


class SEORankingScraper:
    def __init__(self, keywords=None, domains=None, alert_improve=None, **kwargs):
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.domains = domains or DEFAULT_DOMAINS
        self.alert_improve = alert_improve
        self.data_lake_uri = kwargs.get("data_lake_uri")
        self.output_dir = Path(kwargs.get("output_dir") or OUTPUT_DIR)

    async def run(self, **kwargs):
        result = run_live_ingest(
            keywords=self.keywords,
            target_domains=self.domains,
            output_dir=Path(kwargs.get("output_dir") or self.output_dir),
            alert_improve=self.alert_improve,
            write_history=True,
            data_lake_uri=kwargs.get("data_lake_uri", self.data_lake_uri),
            project_csv=True,
        )
        return [{"source": "seo_rankings", "count": result["keywords"], "lake": True}]


if __name__ == "__main__":
    raise SystemExit(main())
