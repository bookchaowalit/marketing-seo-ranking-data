# Upgrade plan — book-seo-data

Score: 8/10 -> 8.5/10 — rankings now match target hosts exactly (no lookalike/substring hits), invalid ranks and duplicate domains never reach Bronze, projections are atomic and CLI input is validated; remaining gaps are packaging polish.

## Backlog

- P0: Confirm GitHub Actions `CI` is green once this work reaches `main` (it downloads the
  pinned `solo-empire-data-lake` tarball). The workflow triggers only on `main` pushes and PRs,
  so `claude/untitled-session-bhlj06` has been verified locally only; keep it required on `main`.
- P1: When `solo-empire-data-lake` moves, bump the pinned commit in `[lake]` together with
  the other book-*-data repos (same SHA everywhere).
- ~~P2: Add a `dev` extra and a `[build-system]` table~~ done 2026-10-22 (SPEC-001: uv.lock,
  dev extra, setuptools backend).
- P2: `load_previous_rankings` keys by keyword only; key by keyword + target domain so
  multi-domain runs compare like with like.

## Docs sync (2026-10-01)

- Closed backlog P1 "cover `fetch_public_page_search_raw` extraction": already covered by
  `tests/test_search_host_filter.py` (`/url?q=` unwrapping, google/youtube exclusion, lookalike hosts).

## Done in this pass (pass 4: edge cases)

- `config.env_bool` returned False for anything but a true-ish word, so
  `FREE_ONLY=` (blank line in .env/compose) or a typo silently disabled the
  free-only guard; blank/unrecognised values now keep the safe default
  (`EnvBoolTests` in `tests/test_edge_cases.py`).
- `quality.finite_number` raised `OverflowError` for a JSON integer beyond
  float range (`1` followed by 400 zeros); it now returns `None`.
- `scripts/scrape_seo_rankings._clean_text` ran `html.unescape` on text that
  BeautifulSoup had already decoded, so a page title showing a literal
  `&lt;b&gt;` was stored as `<b>`; entities are now decoded once. A title made
  only of U+200B/U+2060/BOM/soft hyphen now counts as blank (`data_status`
  `error`) instead of a successful capture.
- Verified: `tests/test_edge_cases.py` (all 3 fail on the old code); full
  suite 43 passed; ruff 0.15.8 + 0.16.9.
- Bumped the `[lake]` pin `68fb5a9` -> `4c24c66` (NDJSON/BOM/U+2028/double-decode fixes); 45 passed with the new package; ruff 0.15.8 + 0.16.9 clean.

## Done in this pass (pass 3)

- `check_ranking` matches by host/subdomain (`domain_matches`) instead of a substring of the
  URL, which counted lookalike and spoofed hosts (and query strings) as rankings.
- `lake.valid_rank` + `ranking_records_from_check`: non-numeric/NaN/zero/fractional ranks are
  ignored instead of crashing `int()`, duplicate/blank target domains are collapsed, and
  `found` requires a valid rank (new `quality` module).
- CLI: domains normalised/de-duplicated, keywords de-duplicated, empty lists and
  `--alert-improve < 1` rejected (exit 2).
- New `fsutil` module: rankings CSV replaced atomically, history appended via atomic rewrite.
- README Quick start uses `pip install -e ".[lake]"`; new "Data quality" section.
- Verified: `pytest -q -rs` 36 passed, 0 skipped (was 27) with the `[lake]` venv; ruff
  0.15.8 and 0.16.9 clean.
- Refresh auth: `/v1/refresh` compares the bearer token with `hmac.compare_digest`
  (`_refresh_token_ok`) instead of `==`, which leaked the matching prefix
  length through timing; `tests/test_refresh_token_compare.py` pins it.
- CSV projection stamps (`_projection_timestamp`) are UTC; they were host-local
  but `product_store.parse_ts` reads naive stamps as UTC, so freshness was off
  by the host offset (`tests/test_projection_timestamp_utc.py`).
- Public-page search skips Google/YouTube *hosts* via `domain_matches`; the
  substring test also dropped results that only mention google.com in their
  path or query (`tests/test_search_host_filter.py`).

## Done in pass 2

- Added a `[lake]` extra pinning `solo-empire-data-lake` at `68fb5a9` (plus pyarrow/duckdb); CI
  installs `-e ".[lake]"`, asserts `data_lake` imports, and lake tests now run instead of skipping.
- Test guards use `lake.shared_runtime_available()` (`importlib.util.find_spec("data_lake")`,
  then the `SOLO_DATA_LAKE_ROOT` / parent-checkout fallback) instead of a parent-checkout lookup.
- `[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]` pins the classic rule set: unpinned
  ruff 0.16 widened its defaults and would have failed `ruff check .` in CI.
- `tests/test_fixture_headers.py` pins `fixtures/*.csv` headers to the CSV projection;
  the fixtures had drifted (missing `target_domain`, `location`, `device`, `provider`, `rank`, ...) and were refreshed.
- `lake.utc_now_iso` is local (same format as the adapter), so projection works without the lake runtime.
- Verified: fresh venv `pip install -e ".[lake]"` from the pinned tarball — 0 skipped of 27 with `[lake]` (was 12 of 26 skipped);
  also against `pip install /home/user/solo-empire-data-lake`, with `SOLO_DATA_LAKE_ROOT=<data-lake>`
  (parent adapter wins), and without the extra (lake tests skip with a reason). ruff 0.15 and 0.16 clean.

## Done in pass 1

- the lake adapter lookup now fails cleanly when the shared `data_lake`
  adapter cannot be imported (it raised `ModuleNotFoundError` before), and the
  adapter loader also honours `SOLO_DATA_LAKE_ROOT` so a sibling clone of the parent
  repo works without editing `PYTHONPATH`.
- CI: installs `pytest ruff`, runs `ruff check .` and `python -m pytest -q -rs`
  (skip reasons visible) instead of bare `unittest`.
- Fixed the remaining default-rule ruff findings; `.gitignore` covers egg-info/ruff cache.
- README "Tests" section documents the standalone and full-lake commands.
- Verified: clean venv with `pip install -e . pytest` (CI shape) and a full run with
  `SOLO_DATA_LAKE_ROOT=<data-lake>` + pyarrow/duckdb (all lake tests execute and pass).
- Contract envelope and fail-closed tests skip cleanly without the adapter; the fail-closed
  test uses an explicit temporary lake URI.
