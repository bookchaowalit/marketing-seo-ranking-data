"""Lake-first adapter for book-seo-data (per provider/query landing)."""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config
from . import quality


def _load_shared():
    """Import the shared product adapter.

    Uses the installed ``solo-empire-data-lake`` package (the ``[lake]`` extra).
    Falls back to a data-lake checkout named by ``SOLO_DATA_LAKE_ROOT``.
    """
    if importlib.util.find_spec("data_lake") is None and config.SOLO_DATA_LAKE_ROOT:
        scripts = (
            Path(config.SOLO_DATA_LAKE_ROOT).expanduser().resolve()
            / "infra"
            / "scripts"
        )
        if not (scripts / "data_lake" / "product_adapter.py").is_file():
            raise ImportError(
                f"SOLO_DATA_LAKE_ROOT={config.SOLO_DATA_LAKE_ROOT} has no "
                "infra/scripts/data_lake/product_adapter.py"
            )
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
    try:
        from data_lake import product_adapter as pa
    except ImportError as exc:
        raise ImportError(
            "shared data_lake runtime not found: install the [lake] extra "
            "(pip install -e .[lake]) or set SOLO_DATA_LAKE_ROOT to a "
            "data-lake checkout"
        ) from exc
    return pa


_pa = None


def _pa_mod():
    global _pa
    if _pa is None:
        _pa = _load_shared()
    return _pa


class LakeUnavailable(RuntimeError):
    pass


class LakeIngestError(RuntimeError):
    pass


def _sync_exc():
    pa = _pa_mod()
    global LakeUnavailable, LakeIngestError
    LakeUnavailable = pa.LakeUnavailable  # type: ignore
    LakeIngestError = pa.LakeIngestError  # type: ignore


def _contract():
    pa = _pa_mod()
    _sync_exc()
    return pa.LakeProductContract(
        source=config.LAKE_SOURCE,
        domain=config.LAKE_DOMAIN,
        product_schema_version=config.SCHEMA_VERSION,
        privacy_class=config.LAKE_PRIVACY_CLASS,
        retention_class=config.LAKE_RETENTION_CLASS,
        bronze_schema_version=config.LAKE_BRONZE_SCHEMA_VERSION,
        project_root=config.PROJECT_ROOT,
        data_lake_uri=config.DATA_LAKE_URI,
        lineage_filename=config.LINEAGE_FILE,
        datasets=(config.LAKE_DATASET_RANKINGS, config.LAKE_DATASET_HISTORY),
    )


def utc_now_iso() -> str:
    """UTC now as ``YYYY-MM-DDTHH:MM:SSZ`` (same format as the shared adapter).

    Kept local so projections and identity helpers work without the lake runtime.
    """
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def shared_runtime_available() -> bool:
    """Return True when the shared ``data_lake`` runtime can be imported.

    Covers the installed ``[lake]`` extra (``solo-empire-data-lake``) and the
    ``SOLO_DATA_LAKE_ROOT`` sibling-checkout fallback.
    """
    if importlib.util.find_spec("data_lake") is not None:
        return True
    try:
        _pa_mod()
    except ImportError:
        return False
    return True


def default_data_lake_uri(solo_root: Optional[Path] = None) -> str:
    del solo_root
    return _pa_mod().default_data_lake_uri(
        data_lake_uri=config.DATA_LAKE_URI,
        project_root=config.PROJECT_ROOT,
    )


_SLUG = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(value: str) -> str:
    text = _SLUG.sub("-", str(value or "").strip().lower())
    return re.sub(r"-+", "-", text).strip("-.") or "unknown"


def stable_ranking_id(
    *,
    keyword: str,
    target_domain: str,
    location: str = "global",
    device: str = "desktop",
) -> str:
    """Stable id: keyword + target_domain + location + device."""
    return ":".join(
        [
            _slug(keyword),
            _slug(target_domain),
            _slug(location),
            _slug(device),
        ]
    )


def valid_rank(value: Any) -> Optional[int]:
    """Return a SERP position as a positive int, or ``None``.

    Rejects blanks, booleans, NaN/inf, fractions, zero and negatives.
    """
    number = quality.finite_number(value)
    if number is None or number < 1 or number != int(number):
        return None
    return int(number)


def normalize_ranking_record(
    ranking: dict[str, Any],
    *,
    provider: str,
    target_domain: str,
    location: str = "global",
    device: str = "desktop",
    observed_at: Optional[str] = None,
) -> dict[str, Any]:
    observed = observed_at or utc_now_iso()
    keyword = str(ranking.get("keyword") or "").strip()
    domain = (
        str(
            target_domain
            or ranking.get("target_domain")
            or ranking.get("best_domain")
            or ""
        )
        .strip()
        .lower()
    )
    rank = valid_rank(ranking.get("rank", ranking.get("best_rank")))
    result_url = str(
        ranking.get("result_url") or ranking.get("best_url") or ranking.get("url") or ""
    )
    result_title = str(
        ranking.get("result_title")
        or ranking.get("best_title")
        or ranking.get("title")
        or ""
    )[:200]
    # A row is only "found" when it carries a valid position.
    found = rank is not None and ranking.get("found", True) is not False
    rid = stable_ranking_id(
        keyword=keyword,
        target_domain=domain or "none",
        location=location,
        device=device,
    )
    return {
        "id": rid,
        "provider": provider,
        "keyword": keyword,
        "target_domain": domain,
        "location": location,
        "device": device,
        "rank": rank if rank is not None else "",
        "result_url": result_url,
        "result_title": result_title,
        "found": bool(found),
        "total_results": _count_or_blank(ranking.get("total_results", "")),
        "observed_at": observed,
        "event_time": observed,
        "content_type_hint": str(ranking.get("content_type_hint") or "text/html"),
    }


def _count_or_blank(value: Any) -> Any:
    number = quality.non_negative_number(value)
    return int(number) if number is not None and number == int(number) else ""


def ranking_records_from_check(
    ranking: dict[str, Any],
    *,
    provider: str,
    target_domains: list[str],
    location: str = "global",
    device: str = "desktop",
    observed_at: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Expand one keyword check into rows per target_domain."""
    observed = observed_at or utc_now_iso()
    keyword = str(ranking.get("keyword") or "")
    positions = ranking.get("all_positions") or []
    by_domain: dict[str, dict[str, Any]] = {}
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        d = str(pos.get("domain") or "").strip().lower()
        rank = valid_rank(pos.get("rank"))
        if not d or rank is None:
            continue
        prev = by_domain.get(d)
        prev_rank = valid_rank(prev.get("rank")) if prev is not None else None
        if prev_rank is None or rank < prev_rank:
            by_domain[d] = pos

    records: list[dict[str, Any]] = []
    # One row per distinct target domain: duplicates would repeat an id in the batch.
    for d in dict.fromkeys(str(domain).strip().lower() for domain in target_domains):
        if not d:
            continue
        pos = by_domain.get(d)
        if pos:
            records.append(
                normalize_ranking_record(
                    {
                        "keyword": keyword,
                        "rank": pos.get("rank"),
                        "result_url": pos.get("url"),
                        "result_title": pos.get("title"),
                        "found": True,
                        "total_results": ranking.get("total_results"),
                    },
                    provider=provider,
                    target_domain=d,
                    location=location,
                    device=device,
                    observed_at=observed,
                )
            )
        else:
            # Not found for this domain still lands as a snapshot row.
            records.append(
                normalize_ranking_record(
                    {
                        "keyword": keyword,
                        "rank": "",
                        "result_url": "",
                        "result_title": "",
                        "found": False,
                        "total_results": ranking.get("total_results"),
                    },
                    provider=provider,
                    target_domain=d,
                    location=location,
                    device=device,
                    observed_at=observed,
                )
            )
    return records


def ingest_provider_payload(
    *,
    provider: str,
    raw: bytes,
    records: list[dict[str, Any]],
    dataset: str,
    content_type: str = "text/html",
    input_format: str = "html",
    data_lake_uri: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Land one provider/query response; never merge raw bytes across queries."""
    _sync_exc()
    if not records:
        raise LakeIngestError(f"Provider {provider!r} produced zero ranking records")

    fmt = input_format
    ctype = content_type
    if ctype.startswith("text/html") or fmt in {"html", "htm"}:
        fmt = "html"
        ctype = "text/html"

    meta = {
        "provider": provider,
        "capture_mode": "per_provider_query",
        "content_type": ctype,
        "record_count": len(records),
    }
    if metadata:
        meta.update(metadata)

    return _pa_mod().ingest_to_lake(
        _contract(),
        raw=raw,
        records=records,
        dataset=dataset,
        data_lake_uri=data_lake_uri,
        metadata=meta,
        content_type=ctype,
        input_format=fmt,
        provider=provider,
    )


def write_lineage(
    result: dict[str, Any],
    *,
    dataset: str,
    data_dir: Optional[Path] = None,
    provider: str = "",
    query_key: str = "",
) -> Path:
    key = dataset
    if provider:
        key = f"{dataset}::{provider}"
    if query_key:
        key = f"{key}::{_slug(query_key)[:40]}"
    return _pa_mod().write_lineage(
        _contract(),
        result,
        dataset=key,
        data_dir=Path(data_dir or config.DATA_DIR),
    )


def load_lineage(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    return _pa_mod().load_lineage(
        Path(data_dir or config.DATA_DIR),
        filename=config.LINEAGE_FILE,
    )


def bronze_dataset_dir(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
) -> Path:
    return _pa_mod().bronze_dataset_dir(
        _contract(), dataset, data_lake_uri=data_lake_uri
    )


def read_bronze_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_bronze_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def read_iceberg_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_iceberg_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def parse_payload_json(row: dict[str, Any]) -> dict[str, Any]:
    return _pa_mod().parse_payload_json(row)


def select_latest_bronze_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _pa_mod().select_latest_bronze_rows(rows)


def landing_object_bytes(raw_key: str, *, data_lake_uri: Optional[str] = None) -> bytes:
    _sync_exc()
    uri = data_lake_uri or default_data_lake_uri()
    return _pa_mod().landing_object_bytes(uri, raw_key)
