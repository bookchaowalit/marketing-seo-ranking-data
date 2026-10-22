---
id: SPEC-001
title: L2 baseline for marketing-seo-ranking-data
status: in review
created: 2026-10-22
owner: bookchaowalit
---

# SPEC-001: L2 baseline

## Document control

| Field | Value |
|---|---|
| Main-only | yes (ADR-0004 v1.1: red run is local, red and green commits pushed together) |
| Maker | Claude session b-data (2026-10-22) |
| Checker / approver | pending (owner) |

## Problem

The Big Loop scorecard (2026-10-22) had this repo below L2: no lockfile, no type check, no
PRODUCT.md and no error reporting. The scheduled capture logs an unreachable owned domain as
a `data_status=error` row and still exits 0, so a broken homepage is invisible; a run where
no domain answers ends in a bare traceback.

## Behaviour

1. Dependencies resolve from the committed `uv.lock`; CI installs with `uv sync --frozen --all-extras`.
   `requirements-collect.txt` (used by `setup_cron.sh`) lists the same runtime dependencies.
2. `mypy` runs in CI over `src/`, `scripts/` and `tests/` (gradual; strict on `book_seo.observability`).
3. With `SENTRY_DSN` set, `scripts/run_seo.py` sends one `warning` event naming the unreachable
   owned domains, and a failed run sends the exception and exits 1. A clean run sends nothing.
   Without a DSN, Sentry is never initialised. `send_default_pii=False`; no page bodies.
4. `parse_page` treats `rel` link types case-insensitively (`rel="Canonical"`).
5. The product's port, schema version, policy defaults, `data_status` values, required GET
   endpoints and owned-page item fields match the frozen registry `data-product-contracts-v2`.
6. CI fails when total coverage drops below 68%.
7. Sentry events carry no PII, frame locals, request bodies or exception text
   (`include_local_variables=False`, `max_request_body_size="never"`, `before_send=scrub_event`).

## Acceptance

| AC | Given / When / Then | Test |
|---|---|---|
| AC-01 | Given `SENTRY_DSN`, when one owned domain answers 503, then exit 0 and one `warning` event names only that domain (tag `job=seo_rankings`, `send_default_pii=False`) | `tests/test_run_job_sentry.py::test_unreachable_owned_domain_is_reported` |
| AC-02 | Given `SENTRY_DSN`, when no domain is reachable, then one exception event and exit 1 | `test_failed_run_is_reported_and_exits_non_zero` |
| AC-03 | Given no `SENTRY_DSN`, when the run fails, then Sentry is not initialised | `test_without_dsn_sentry_stays_off`, `test_clean_run_sends_nothing` |
| AC-04 | Given saved HTML fixtures, when pages are parsed and a run is written, then title/canonical/status follow DATA-CONTRACT.md, `rank` stays blank and the history header is written once | `tests/test_scrape_page_capture.py` |
| AC-05 | The product matches registry v2 offline | `tests/test_contract_registry_v2.py` |
| AC-07 | Events drop locals, request data and exception text (security review, MEDIUM) | `test_run_job_sentry.py::test_events_carry_no_locals_request_data_or_exception_text` |
| AC-06 | CI on `main` is green on Python 3.11 and 3.13 with the coverage gate | CI run below |

## Red/green log

| AC | Red (sha · command · failure) | Green |
|---|---|---|
| AC-01..03 | `6137fb6` · `python -m pytest -q tests/test_run_job_sentry.py` · `AssertionError: 0 != 1` (no Sentry message), `ValueError` escapes `main()` | [run 39713246132](https://github.com/bookchaowalit/marketing-seo-ranking-data/actions/runs/39713246132) (success, 3.11 + 3.13) |
| AC-04 | `79ef56f` · `python -m pytest -q tests/test_scrape_page_capture.py` · `AssertionError: '' != 'https://chaowalit.com/'` | [run 39713246132](https://github.com/bookchaowalit/marketing-seo-ranking-data/actions/runs/39713246132) (success, 3.11 + 3.13) |
| AC-07 | `0c9bb52` · `python -m pytest -q tests/test_run_job_sentry.py` · `AssertionError: None is not False` | [run 39755298383](https://github.com/bookchaowalit/marketing-seo-ranking-data/actions/runs/39755298383) (success, 3.11 + 3.13) |

## Schedule (planned replacement, D6)

No scheduler was changed (owner decision D6 on the schedule host is open).

- Today: `setup_cron.sh install` can add `0 8 * * * … flock -n data/seo.lock .venv/bin/python
  scripts/run_seo.py` to the local crontab; it is **not** installed on this WSL host
  (2026-10-22). The roadmap's planned Windows task `SoloEmpire-Scraping-seo` (daily 08:30)
  awaits host/install approval; its state is unverified [ต้องยืนยัน].
- Planned replacement once D6 is decided: one hosted daily job that runs
  `python scripts/run_seo.py` with `SENTRY_DSN` and `SENTRY_ENVIRONMENT=production` set,
  followed by `book-seo-data` lake ingest into the chosen lake target, with a freshness check
  against the 30-hour SLO from the roadmap. The crontab line and `setup_cron.sh` are retired
  in the same change.

## Out of scope

Live captures, R2/production lake writes, renaming `seo_rankings` (needs `seo.v2`), Search
Console import, the owned-domain config (roadmap seo-t3), and a deploy workflow (blocked by D6).
