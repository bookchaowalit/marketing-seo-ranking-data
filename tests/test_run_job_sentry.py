"""The scheduled job (scripts/run_seo.py) reports failures to Sentry when SENTRY_DSN is set.

A domain that is unreachable is logged into the CSVs as ``data_status=error`` and the run
still exits 0, so without an explicit report a broken owned page stays invisible. No
network: ``fetch_page`` is a fake and ``sentry_sdk`` is a fake module in ``sys.modules``.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_seo  # noqa: E402
import scrape_seo_rankings as capture  # noqa: E402

OK_HTML = "<html><head><title>Owned</title></head></html>"


class FakeSentry(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sentry_sdk")
        self.init_kwargs: dict[str, Any] | None = None
        self.messages: list[tuple[str, Any]] = []
        self.exceptions: list[BaseException] = []
        self.tags: dict[str, str] = {}

    def init(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def capture_message(self, message: str, level: Any = None) -> None:
        self.messages.append((message, level))

    def capture_exception(self, error: BaseException) -> None:
        self.exceptions.append(error)

    def flush(self, timeout: Any = None) -> None:
        return None


def fake_fetch(statuses: dict[str, int]) -> Any:
    def fetch(domain: str) -> tuple[bytes, dict[str, Any]]:
        status = statuses[domain]
        html = OK_HTML if status == 200 else ""
        return html.encode(), capture.parse_page(
            domain, capture.page_url(domain), status, html
        )

    return fetch


class RunJobSentryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = tmp.name
        self.sentry = FakeSentry()
        patcher = mock.patch.dict(sys.modules, {"sentry_sdk": self.sentry})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, statuses: dict[str, int], dsn: str) -> Any:
        env = {"SENTRY_DSN": dsn} if dsn else {}
        argv = ["run_seo.py", "--output-dir", self.out]
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(capture, "fetch_page", fake_fetch(statuses)),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return run_seo.main()

    def test_unreachable_owned_domain_is_reported(self):
        code = self.run_main(
            {"bookchaowalit.com": 200, "chaowalit.com": 503},
            "https://key@example.invalid/1",
        )
        self.assertEqual(code, 0, "a partial capture still lands the reachable pages")
        self.assertEqual(len(self.sentry.messages), 1)
        message, level = self.sentry.messages[0]
        self.assertIn("chaowalit.com", message)
        self.assertNotIn("bookchaowalit.com", message)
        self.assertEqual(level, "warning")
        self.assertEqual(self.sentry.tags.get("job"), "seo_rankings")
        self.assertIs((self.sentry.init_kwargs or {}).get("send_default_pii"), False)

    def test_failed_run_is_reported_and_exits_non_zero(self):
        code = self.run_main(
            {"bookchaowalit.com": 500, "chaowalit.com": 500},
            "https://key@example.invalid/1",
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(self.sentry.exceptions), 1)
        self.assertIsInstance(self.sentry.exceptions[0], ValueError)

    def test_clean_run_sends_nothing(self):
        code = self.run_main(
            {"bookchaowalit.com": 200, "chaowalit.com": 200},
            "https://key@example.invalid/1",
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.sentry.messages, self.sentry.exceptions), ([], []))
        self.assertIsNotNone(self.sentry.init_kwargs)

    def test_without_dsn_sentry_stays_off(self):
        code = self.run_main({"bookchaowalit.com": 500, "chaowalit.com": 500}, "")
        self.assertEqual(code, 1)
        self.assertIsNone(self.sentry.init_kwargs)
        self.assertEqual((self.sentry.messages, self.sentry.exceptions), ([], []))


if __name__ == "__main__":
    unittest.main()
