# PRODUCT — book-seo-data (`marketing-seo-ranking-data`)

Owned-page reachability and provenance for the owner's public domains, lake-first, served
read-only as `seo.v1` on `127.0.0.1:8106`. Despite the historical dataset name
`seo_rankings`, the scheduled path **does not collect search-engine rankings**: it checks
whether the owner's homepages answer, and records HTTP status, title and canonical link.
Rankings would need Search Console / an authenticated import, which is not built.

## Runtime status (verified 2026-10-22)

| What | Status |
|---|---|
| Scheduled entry point | `scripts/run_seo.py` → `scripts/scrape_seo_rankings.py`: fetches `https://bookchaowalit.com/` and `https://chaowalit.com/`, writes `data/exported/seo_rankings.csv`, `seo_rankings_history.csv` (append) and `seo_rankings_raw.json` (byte counts only, no HTML) |
| Who schedules it | `setup_cron.sh install` would add a daily 08:00 crontab line. This WSL host's crontab has **no** scraper entry (checked 2026-10-22), so it is not running from here. The roadmap names a planned Windows task `SoloEmpire-Scraping-seo` (daily 08:30) awaiting host/install approval; whether it is installed cannot be checked from WSL [ต้องยืนยัน] |
| Lake ingest | `book-seo-data` / `python -m book_seo.ingest` (manual): landing → Bronze `domain=seo/dataset=seo_rankings(_history)` → manifest + lineage via the shared `data_lake` runtime (`[lake]` extra, pinned data-lake-core commit). Not scheduled |
| API | `book-seo-data-api` / `python -m book_seo.api` on `127.0.0.1:8106`, GET-only; `POST /v1/refresh` 403 by default. Started by hand; no hosted deployment |
| Hosted deployment | None. No cloud job, no public endpoint |
| Error reporting | Sentry, opt-in: `sentry-sdk` is a dependency, but nothing is sent unless `SENTRY_DSN` is set. In `run_seo.py` an unreachable owned domain sends one `warning` event; a failed run sends the exception (type and stack only: no locals, request bodies or exception text) and exits 1. No DSN is configured anywhere yet [ต้องยืนยัน] |
| Contract | `seo.v1`, port 8106, in the frozen `data-product-contracts-v2.yaml`; owned-page semantics in [DATA-CONTRACT.md](DATA-CONTRACT.md). Checked offline by `tests/test_contract_registry_v2.py` and `tests/test_contract_freeze.py` |
| Quality gates in CI | ruff, ruff format, mypy (gradual), offline pytest with coverage ≥ 68% on Python 3.11 and 3.13 (lake tests included through the `[lake]` extra), compile + CLI smoke, shared security scan |

## Users and consumers

- The owner: is each owned homepage up, with the right title and canonical link?
- Registry consumers of port 8106: `market-insights-web` (insights dashboard) and the
  portfolio frontend catalog. Neither reads the API live today (loopback only).

## Scope

In: owner-controlled public homepages, bounded (≤ 10 domains, ≤ 20 labels per run), free
providers only (`public_page_check`, `offline_fixtures`).
Out: SERP scraping, paid providers (`firecrawl`, `google_cse`, `paid_serp` are blocked by
policy before any network call), Search Console / GA4 / Ahrefs imports.

## Known gaps

- Dataset name `seo_rankings` suggests rankings; renaming needs a new schema version (roadmap seo-t1).
- The owned-domain list is hard-coded in `scripts/run_seo.py`; no versioned config that
  refuses non-owned URLs yet (roadmap seo-t3).
- No Silver quality gate in lineage (Bronze → API only).
- Coverage 68.54% (gate 68); the 80% floor needs tests for `ingest.py`, `api.py` and `http_client.py`.
- No hosting target for the schedule until owner decision D6; see
  [specs/001-l2-baseline/spec.md](specs/001-l2-baseline/spec.md).
