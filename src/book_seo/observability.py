"""Opt-in Sentry for the scheduled owned-page capture (scripts/run_seo.py).

Off unless ``SENTRY_DSN`` is set and ``sentry_sdk`` is importable. The capture logs an
unreachable owned domain as a ``data_status=error`` row and still exits 0, so the job
reports that explicitly (one ``warning`` event per run) and reports a failed run as an
exception. No page bodies, payloads or personal data are attached: only the job name,
the owned domain names and the error.
"""

from __future__ import annotations

import os
from importlib import import_module
from types import ModuleType


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
        traces_sample_rate=0.0,
    )
    sdk.set_tag("job", job)
    return sdk


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
