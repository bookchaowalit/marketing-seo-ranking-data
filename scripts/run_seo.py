#!/usr/bin/env python3
"""Run owned-domain public-page SEO checks for this repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# The cron venv (setup_cron.sh) installs requirements-collect.txt only, not this package.
sys.path.insert(0, str(ROOT / "src"))

from book_seo.observability import (  # noqa: E402
    init_sentry,
    report_exception,
    report_message,
)
from scrape_seo_rankings import SEORankingScraper  # noqa: E402

JOBS = (("seo_rankings", SEORankingScraper),)


async def run_seo(output_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, cls in JOBS:
        scraper = cls(
            domains=["bookchaowalit.com", "chaowalit.com"],
            keywords=["python developer thailand"],
            output_dir=output_dir,
        )
        batch = await scraper.run()
        results.extend(batch)
        print(f"[run_seo] {name}: {batch[0].get('count') if batch else 0}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run book-seo-data public-page checks")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "exported")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    sdk = init_sentry("seo_rankings")
    try:
        results = asyncio.run(run_seo(args.output_dir))
    except Exception as exc:  # reported, then the run exits non-zero
        report_exception(sdk, exc)
        print(f"[run_seo] failed: {exc}", file=sys.stderr)
        return 1
    failed = [domain for batch in results for domain in batch.get("failed_domains", [])]
    if failed:
        report_message(
            sdk,
            f"seo_rankings: {len(failed)} owned domain(s) unreachable: {', '.join(failed)}",
        )
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
