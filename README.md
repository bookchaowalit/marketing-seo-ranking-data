# book-seo-data

Owned-page reachability and provenance — **lake-first**, public-page collection
only by default. The historical `seo_rankings` dataset name does not imply
that a search-engine result position was collected.

Migration status: shared `data_lake.product_adapter`. The scheduled capture
retains page metadata, response byte counts and CSV projections; it does not
archive original HTML. Lake replay can land the exact capture-file bytes, and
Bronze retains observation fields with `raw_object_key` for that artifact. See the
[dataset contract](DATA-CONTRACT.md) for the distinction between scheduled
owned-page observations and separately supplied search-result observations.

Collection cron: `bash setup_cron.sh install` runs owned-domain homepage checks daily at 08:00 into `data/exported/`. This does not collect SERP ranks. Lake ingest remains a separate command.

## Data contract

| Field | Value |
|---|---|
| `source` | `book-seo-data` |
| `domain` | `seo` |
| `datasets` | `seo_rankings`, `seo_rankings_history` |
| Bronze schema | `1` |
| envelope | `seo.v1` |
| port | `8106` |

```text
Scheduled capture CSV/metadata / separately evidenced provider response
    → landing/source=book-seo-data/provider=<name>/…
    → bronze/domain=seo/dataset=seo_rankings|seo_rankings_history
    → manifest + lineage
    → DuckDB API
    → optional CSV projection
```

### Stable record identity

`keyword : target_domain : location : device`

Fields on Bronze/API items:

- `capture_kind`, `provider`, `result_url`, `result_title`, `observed_at`
- `rank` stays blank for `owned_page_provenance`; it is meaningful only for
  separately evidenced `serp_ranking` observations
- `event_time` = observation time (`observed_at`)

### Provider policy

| Provider | Default | Notes |
|---|---|---|
| `public_page_check` | free | Direct public-page checks |
| `offline_fixtures` | free | Fixture / dry-run |
| `firecrawl` | **blocked** | Before network |
| `google_cse` | **blocked** | Billing path |
| `paid_serp` | **blocked** | Paid SERP APIs |

## Quick start

```bash
pip install -e .
# Lake writes need the shared data-lake runtime: the [lake] extra installs the
# pinned solo-empire-data-lake (plus pyarrow/duckdb); inside Solo Empire the
# parent checkout's infra/scripts/data_lake is used instead
pip install -e ".[lake]"

python -m book_seo.ingest --fixture
python -m book_seo.ingest --keywords "python developer" --domain bookchaowalit.com \
  --data-lake-uri /path/to/data/lake

python -m book_seo.api --host 127.0.0.1 --port 8106
```

Lake failure exits non-zero and does **not** update `seo_rankings.csv`.

## Optional Iceberg REST catalog read pilot

SEO uses the shared `product_store` feature flag. Direct Bronze Parquet remains
the default:

| Variable | Default | Meaning |
|---|---|---|
| `LAKE_READ_MODE` | `parquet` | Direct Bronze read; `iceberg` resolves the registered catalog table and queries it with DuckDB |
| `LAKE_READ_FALLBACK` | `error` | Fail-closed by default; `parquet` is an explicit fallback |
| `ICEBERG_CATALOG_URI` | unset | REST/SQL catalog URI from the runtime secret provider |
| `ICEBERG_WAREHOUSE_URI` | unset | Provider-issued warehouse URI/name |
| `ICEBERG_CATALOG_TOKEN` | unset | Catalog credential; never store it in the repository or API response |

The matching tables must be registered before enabling Iceberg mode:
`bronze.seo_seo_rankings` and `bronze.seo_seo_rankings_history`.

```bash
npx --yes @infisical/cli@0.43.120 run \
  --projectId=<solo-empire-project-id> --env=dev --path=/cloudflare -- \
  bash -lc 'export SOLO_EMPIRE_DATA_LAKE_URI=s3://<bucket>/<prefix>; \
    export LAKE_READ_MODE=iceberg; export LAKE_READ_FALLBACK=error; \
    PYTHONPATH=src /path/to/solo-empire/.venv/bin/python \
    -m book_seo.api --host 127.0.0.1 --port 8106'
```

Successful `/v1/metadata` reports
`storage_model=lake_first_iceberg_rest_duckdb` and both source kinds as
`iceberg_rest_duckdb`. CSV remains CLI-only and is not a fallback unless the
explicit `LAKE_READ_FALLBACK=parquet` flag is selected.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/metadata` | `storage_model=lake_first_bronze_duckdb` (Parquet) or `lake_first_iceberg_rest_duckdb` (Iceberg flag) |
| GET | `/v1/records` | Bronze latest (not CSV) |
| GET | `/v1/history` | Bronze history |
| POST | `/v1/refresh` | 403 by default |

## Data quality

- A result counts for a target domain only when its host *is* that domain
  or a subdomain (`ingest.domain_matches`); the old substring test matched
  `notexample.com` and `example.com.evil.net` for `example.com`.
- Ranks must be positive integers (`lake.valid_rank`); a row without a valid
  rank is `found=false`, and a negative/non-integer `total_results` is blanked.
- Target domains are normalised (`https://www.Example.com/` -> `example.com`)
  and de-duplicated, so one check never lands two rows with the same id.
- `--keywords`/`--domains` must be non-empty after trimming (keywords are
  de-duplicated case-insensitively); `--alert-improve` must be >= 1 (exit 2).
- CSV projections are written atomically (temp file + `os.replace`).

## Tests

```bash
# What CI runs (uv 0.13+): uv.lock installs the package, the dev tools and the [lake]
# extra (pinned solo-empire-data-lake runtime), so Bronze/Silver lake tests run standalone
uv sync --frozen --all-extras
uv run ruff check . && uv run ruff format --check .
uv run mypy                                   # scope: [tool.mypy] files in pyproject.toml
FREE_ONLY=true uv run python -m pytest -q -rs --cov   # CI gate: total coverage >= 68%

# Without the [lake] extra: SOLO_DATA_LAKE_ROOT points at a data-lake
# checkout whose infra/scripts/data_lake supplies the shared runtime
SOLO_DATA_LAKE_ROOT=/path/to/solo-data-lake python -m pytest -q
```

All tests are offline: page captures use saved HTML under `fixtures/pages/`.
`requirements-collect.txt` is the cron venv's install list (`setup_cron.sh`); keep it in
sync with `[project] dependencies`.

## Error reporting

Set `SENTRY_DSN` (and optionally `SENTRY_ENVIRONMENT`) for `scripts/run_seo.py` to report an
unreachable owned domain (warning) or a failed run (exception, exit 1). Off when unset.
See [PRODUCT.md](PRODUCT.md) for what runs today.
