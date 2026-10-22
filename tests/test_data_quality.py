"""Data-quality guards, domain matching and CLI validation for book-seo-data."""

from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_seo import ingest, lake
from book_seo.fsutil import atomic_write_csv

OBSERVED = "2026-08-01T12:00:00Z"


class DomainMatchTests(unittest.TestCase):
    def test_host_or_subdomain_only(self):
        yes = [
            ("https://example.com/a", "example.com"),
            ("https://www.example.com/a", "example.com"),
            ("https://blog.example.com", "https://www.Example.com/"),
            ("http://EXAMPLE.com:8080/x", "example.com"),
        ]
        no = [
            ("https://notexample.com/a", "example.com"),
            ("https://example.com.evil.net/a", "example.com"),
            ("https://evil.net/?next=example.com", "example.com"),
            ("not a url", "example.com"),
            ("https://example.com", ""),
            ("http://[::1", "example.com"),
        ]
        for url, domain in yes:
            with self.subTest(url=url, domain=domain):
                self.assertTrue(ingest.domain_matches(url, domain))
        for url, domain in no:
            with self.subTest(url=url, domain=domain):
                self.assertFalse(ingest.domain_matches(url, domain))

    def test_check_ranking_ignores_lookalike_hosts(self):
        results = [
            {"url": "https://notexample.com/x", "title": "Lookalike"},
            {"url": "https://example.com.evil.net/x", "title": "Spoof"},
            {"url": "https://docs.example.com/y", "title": "Real"},
        ]
        ranking = ingest.check_ranking("kw", ["example.com"], results=results)
        self.assertEqual(ranking["best_rank"], 3)
        self.assertEqual([p["rank"] for p in ranking["all_positions"]], [3])


class RankingRecordQualityTests(unittest.TestCase):
    def test_valid_rank(self):
        self.assertEqual(lake.valid_rank("3"), 3)
        self.assertEqual(lake.valid_rank(1.0), 1)
        for bad in (None, "", 0, -2, 2.5, float("nan"), float("inf"), "top", True):
            with self.subTest(bad=bad):
                self.assertIsNone(lake.valid_rank(bad))

    def test_invalid_rank_is_not_found(self):
        rec = lake.normalize_ranking_record(
            {"keyword": "kw", "rank": float("nan"), "found": True, "total_results": -1},
            provider="public_page_check",
            target_domain="example.com",
            observed_at=OBSERVED,
        )
        self.assertEqual(
            (rec["rank"], rec["found"], rec["total_results"]), ("", False, "")
        )

    def test_duplicate_domains_and_bad_positions(self):
        ranking = {
            "keyword": "kw",
            "total_results": 20,
            "all_positions": [
                {
                    "domain": "example.com",
                    "rank": "x",
                    "url": "https://example.com/bad",
                },
                {"domain": "Example.com", "rank": 7, "url": "https://example.com/7"},
                {"domain": "example.com", "rank": 4, "url": "https://example.com/4"},
                "garbage",
            ],
        }
        records = lake.ranking_records_from_check(
            ranking,
            provider="public_page_check",
            target_domains=["example.com", "EXAMPLE.com ", "other.org", ""],
            observed_at=OBSERVED,
        )
        self.assertEqual(
            [r["target_domain"] for r in records], ["example.com", "other.org"]
        )
        self.assertEqual(len({r["id"] for r in records}), 2)
        self.assertEqual(
            (records[0]["rank"], records[0]["result_url"]), (4, "https://example.com/4")
        )
        self.assertEqual((records[1]["rank"], records[1]["found"]), ("", False))


class CliValidationTests(unittest.TestCase):
    def _exit_code(self, argv: list[str]) -> str | int | None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            ingest.main(argv)
        return ctx.exception.code

    def test_rejects_empty_inputs_and_bad_alert(self):
        self.assertEqual(self._exit_code(["--keywords", " , "]), 2)
        self.assertEqual(self._exit_code(["--domains", ","]), 2)
        self.assertEqual(self._exit_code(["--alert-improve", "0"]), 2)

    def test_inputs_are_normalized(self):
        with mock.patch.object(ingest, "run_live_ingest") as run:
            code = ingest.main(
                [
                    "--keywords",
                    "Python Tips, python tips,SEO",
                    "--domains",
                    "https://www.Example.com/,example.com,b.org",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["keywords"], ["Python Tips", "SEO"])
        self.assertEqual(
            run.call_args.kwargs["target_domains"], ["example.com", "b.org"]
        )


class AtomicProjectionTests(unittest.TestCase):
    def test_projections_leave_no_temp_files(self):
        rows = [
            {
                "keyword": "kw",
                "target_domain": "example.com",
                "rank": 3,
                "found": True,
                "extra": 1,
            }
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            out = Path(tmp)
            ingest.project_rankings_csv(rows, out)
            ingest.project_history_csv(rows, out)
            ingest.project_history_csv(rows, out)
            self.assertEqual(
                sorted(p.name for p in out.iterdir()),
                ["seo_rankings.csv", "seo_rankings_history.csv"],
            )
            with (out / "seo_rankings_history.csv").open(
                newline="", encoding="utf-8"
            ) as f:
                self.assertEqual([r["rank"] for r in csv.DictReader(f)], ["3", "3"])

    def test_failed_write_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            atomic_write_csv(path, ["a"], [{"a": 1}])
            with self.assertRaises(ValueError):
                atomic_write_csv(path, ["a"], [{"b": 2}])
            self.assertEqual(path.read_bytes(), b"a\r\n1\r\n")


if __name__ == "__main__":
    unittest.main()
