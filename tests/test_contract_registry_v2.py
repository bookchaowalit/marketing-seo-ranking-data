"""seo.v1 checked offline against the frozen data-product registry (generation 2).

The registry lives in the private architecture-docs repo
(``systems/architecture/data-product-contracts-v2.yaml``, freeze_id
``data-product-contracts-v2``, status frozen). CI cannot read it, so the values this
product must honour are copied below (copied 2026-10-22). A registry change needs a new
freeze generation, so these constants only change together with it.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from book_seo import config, store

ROOT = Path(__file__).resolve().parents[1]

REGISTRY_PRODUCT = {
    "id": "seo",
    "port": 8106,
    "schema_version": "seo.v1",
    "package": "book_seo",
}
REGISTRY_DATA_STATUS = {
    "ok",
    "empty",
    "stale",
    "malformed",
    "not_found",
    "forbidden",
    "error",
    "accepted",
}
REGISTRY_ENVELOPE_KEYS = [
    "schema_version",
    "source",
    "retrieved_at",
    "data_status",
    "items",
    "next_cursor",
]
REGISTRY_GET_ENDPOINTS = [
    "/healthz",
    "/v1/metadata",
    "/v1/records",
    "/v1/records/",
    "/v1/history",
]
REGISTRY_POLICY_DEFAULTS = {
    "FREE_ONLY": True,
    "ALLOW_PAID_PROVIDERS": False,
    "ALLOW_EXTERNAL_WRITES": False,
    "ALLOW_REFRESH": False,
}

# Owned-page observation fields promised by DATA-CONTRACT.md for Bronze/API items.
OWNED_PAGE_ITEM_FIELDS = [
    "capture_kind",
    "provider",
    "keyword",
    "target_domain",
    "result_url",
    "result_title",
    "found",
    "rank",
    "best_rank",
    "total_results",
    "page_http_status",
    "page_data_status",
    "page_canonical_url",
    "page_content_length",
    "observed_at",
    "event_time",
    "raw_object_key",
]


class RegistryV2Tests(unittest.TestCase):
    def test_port_schema_and_package(self):
        self.assertEqual(int(config.API_PORT), REGISTRY_PRODUCT["port"])
        self.assertEqual(config.SCHEMA_VERSION, REGISTRY_PRODUCT["schema_version"])
        self.assertTrue((ROOT / "src" / str(REGISTRY_PRODUCT["package"])).is_dir())

    def test_policy_defaults(self):
        for name, value in REGISTRY_POLICY_DEFAULTS.items():
            self.assertIs(getattr(config, name), value, name)

    def test_every_data_status_literal_is_registered(self):
        used: set[str] = set()
        for path in (ROOT / "src" / "book_seo").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            used |= set(re.findall(r'data_status\s*=\s*"([a-z_]+)"', text))
            used |= set(re.findall(r'"data_status":\s*"([a-z_]+)"', text))
        self.assertTrue(used, "no data_status literals found; the scan is broken")
        self.assertLessEqual(used, REGISTRY_DATA_STATUS)

    def test_required_get_endpoints_are_routed(self):
        api = (ROOT / "src" / "book_seo" / "api.py").read_text(encoding="utf-8")
        for path in REGISTRY_GET_ENDPOINTS:
            self.assertRegex(
                api, rf'path(\.startswith\(| == )"{re.escape(path)}"', path
            )

    def test_envelope_keys_from_shared_runtime(self):
        try:
            body = store.envelope(items=[], data_status="empty")
        except ImportError:
            self.skipTest(
                "shared data_lake runtime not installed (uv sync --all-extras)"
            )
        self.assertEqual(
            sorted(REGISTRY_ENVELOPE_KEYS),
            sorted(k for k in body if k in REGISTRY_ENVELOPE_KEYS),
        )
        self.assertIn(body["data_status"], REGISTRY_DATA_STATUS)

    def test_owned_page_item_carries_the_documented_fields(self):
        payload = {
            "capture_kind": "owned_page_provenance",
            "provider": "public_page_check",
            "keyword": "owned-page|https://bookchaowalit.com/",
            "target_domain": "bookchaowalit.com",
            "rank": "",
            "result_url": "https://bookchaowalit.com/",
            "result_title": "Book Chaowalit",
            "found": True,
            "page_http_status": 200,
            "page_canonical_url": "https://bookchaowalit.com/",
            "page_content_length": 1234,
            "page_data_status": "ok",
            "observed_at": "2026-10-22T01:00:00Z",
        }
        row = {
            "payload_json": json.dumps(payload),
            "event_time": "2026-10-22T01:00:00Z",
            "raw_object_key": "landing/source=book-seo-data/provider=public_page_check/x.csv",
        }
        try:
            item = store.seo_item_from_bronze(row)
        except ImportError:
            self.skipTest(
                "shared data_lake runtime not installed (uv sync --all-extras)"
            )
        for field in OWNED_PAGE_ITEM_FIELDS:
            self.assertIn(field, item, field)
        self.assertEqual(item["capture_kind"], "owned_page_provenance")
        # Unknown rank is blank, never 0 or 1.
        self.assertEqual((item["rank"], item["best_rank"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
