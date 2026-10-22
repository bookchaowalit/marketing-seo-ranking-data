"""Opt-in Sentry for the scheduled owned-page capture (scripts/run_seo.py).

Off unless ``SENTRY_DSN`` is set and ``sentry_sdk`` is importable. The capture logs an
unreachable owned domain as a ``data_status=error`` row and still exits 0, so the job
reports that explicitly (one ``warning`` event per run) and reports a failed run as an
exception. Events carry the job name, the owned domain names and the exception type and
stack: no PII, frame locals or request bodies, and ``scrub_event`` replaces exception
messages (which can hold URLs or tokens).
"""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType
from typing import Any


def init_sentry(job: str) -> ModuleType | None:
    """Initialise Sentry for one job run; return the SDK, or None when it is off."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return None
    try:
        sdk = import_module("sentry_sdk")
    except ImportError:
        return None
    sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub_event,
    )
    sdk.set_tag("job", job)
    return sdk


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Drop request data, frame locals and exception messages; keep the type and stack."""
    event.pop("request", None)
    event.pop("extra", None)
    for value in (event.get("exception") or {}).get("values") or []:
        if value.get("value"):
            value["value"] = "[redacted]"
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


def report_exception(sdk: ModuleType | None, error: BaseException) -> None:
    """Send a failed run as one exception event."""
    if sdk is not None:
        sdk.capture_exception(error)
        sdk.flush(timeout=5)


def report_message(
    sdk: ModuleType | None, message: str, level: str = "warning"
) -> None:
    """Send one message event (e.g. a partial capture)."""
    if sdk is not None:
        sdk.capture_message(message, level=level)
        sdk.flush(timeout=5)
