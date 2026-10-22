"""Scheduled owned-page capture (scripts/scrape_seo_rankings.py) against saved HTML fixtures.

No network: ``fetch_page`` is replaced by a fake that parses files under tests/fixtures/pages/.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "tests" / "fixtures" / "pages"
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_seo_rankings as capture  # noqa: E402


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def fake_fetch(pages: dict[str, tuple[int, str]]) -> Any:
    def fetch(domain: str) -> tuple[bytes, dict[str, Any]]:
        status, html = pages[domain]
        return html.encode("utf-8"), capture.parse_page(
            domain, capture.page_url(domain), status, html
        )

    return fetch


class ParsePageTests(unittest.TestCase):
    def test_owned_page_title_and_canonical(self):
        row = capture.parse_page(
            "bookchaowalit.com",
            "https://bookchaowalit.com/",
            200,
            page("owned_home.html"),
        )
        self.assertEqual(row["title"], "Book Chaowalit | Full-stack developer")
        self.assertEqual(row["canonical"], "https://bookchaowalit.com/")
        self.assertEqual(row["data_status"], "ok")
        self.assertEqual(row["source"], "public_page_check")
        # Owned-page provenance never carries a search rank or keyword.
        self.assertEqual((row["rank"], row["keyword"]), ("", ""))
        self.assertEqual(row["content_length"], len(page("owned_home.html")))

    def test_canonical_rel_is_case_insensitive(self):
        # HTML link types are ASCII case-insensitive, so rel="Canonical" is a canonical link.
        row = capture.parse_page(
            "chaowalit.com",
            "https://chaowalit.com/",
            200,
            page("canonical_mixed_case.html"),
        )
        self.assertEqual(row["canonical"], "https://chaowalit.com/")

    def test_invisible_title_counts_as_missing(self):
        row = capture.parse_page(
            "chaowalit.com", "https://chaowalit.com/", 200, page("invisible_title.html")
        )
        self.assertEqual(row["title"], "")
        self.assertEqual(row["data_status"], "error")

    def test_http_error_is_not_ok_even_with_title(self):
        row = capture.parse_page(
            "bookchaowalit.com",
            "https://bookchaowalit.com/",
            503,
            page("owned_home.html"),
        )
        self.assertEqual(row["data_status"], "error")
        self.assertEqual(row["http_status"], 503)


class NormalizeRequestTests(unittest.TestCase):
    def test_domains_are_normalised_and_deduplicated(self):
        domains, keywords = capture.normalize_request(
            [
                "https://www.BookChaowalit.com/about",
                "bookchaowalit.com",
                "chaowalit.com:443",
            ],
            "a, b, a",
        )
        self.assertEqual(domains, ["bookchaowalit.com", "chaowalit.com"])
        self.assertEqual(keywords, ["a", "b"])

    def test_invalid_or_too_many_domains_rejected(self):
        with self.assertRaises(ValueError):
            capture.normalize_request(["not a domain"], None)
        with self.assertRaises(ValueError):
            capture.normalize_request(
                [f"d{i}.example.com" for i in range(capture.MAX_DOMAINS + 1)], None
            )


class RunCaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)

    def run_capture(self, pages: dict[str, tuple[int, str]]) -> list[dict[str, Any]]:
        scraper = capture.SEORankingScraper(
            domains=list(pages), keywords=["k"], output_dir=self.out
        )
        with mock.patch.object(capture, "fetch_page", fake_fetch(pages)):
            return asyncio.run(scraper.run())

    def test_snapshot_history_and_raw_follow_the_contract(self):
        pages = {
            "bookchaowalit.com": (200, page("owned_home.html")),
            "chaowalit.com": (200, page("canonical_mixed_case.html")),
        }
        result = self.run_capture(pages)
        self.run_capture(pages)
        self.assertEqual(result[0]["count"], 2)

        with (self.out / "seo_rankings.csv").open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, capture.SNAPSHOT_FIELDS)
            rows = list(reader)
        self.assertEqual(
            [r["domain"] for r in rows], ["bookchaowalit.com", "chaowalit.com"]
        )
        self.assertTrue(
            all(r["rank"] == "" and r["source"] == "public_page_check" for r in rows)
        )
        self.assertTrue(all(r["captured_at"].endswith("Z") for r in rows))

        history = (
            (self.out / "seo_rankings_history.csv")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(history[0].split(","), capture.HISTORY_FIELDS)
        self.assertEqual(
            len(history), 1 + 2 * 2, "history header is written once, rows are appended"
        )

        raw = json.loads(
            (self.out / "seo_rankings_raw.json").read_text(encoding="utf-8")
        )
        self.assertIs(raw["rank_collected"], False)
        self.assertEqual(
            raw["pages"]["bookchaowalit.com"],
            len(page("owned_home.html").encode("utf-8")),
        )

    def test_no_reachable_domain_fails_without_writing(self):
        with self.assertRaises(ValueError):
            self.run_capture(
                {
                    "bookchaowalit.com": (500, ""),
                    "chaowalit.com": (200, page("invisible_title.html")),
                }
            )
        self.assertFalse((self.out / "seo_rankings.csv").exists())


if __name__ == "__main__":
    unittest.main()
