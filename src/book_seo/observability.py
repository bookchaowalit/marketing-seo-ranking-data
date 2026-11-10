"""Opt-in Sentry for the scheduled owned-page capture (scripts/run_seo.py).

Off unless ``SENTRY_DSN`` is set and ``sentry_sdk`` is importable. The capture logs an
unreachable owned domain as a ``data_status=error`` row and still exits 0, so the job
reports that explicitly (one ``warning`` event per run) and reports a failed run as an
exception. Events carry the job name, the owned domain names and the exception type and
stack: no PII, frame locals or request bodies, and ``scrub_event`` blanks exception
messages (which can hold URLs or tokens).

Log text (2026-11-10): the default ``LoggingIntegration`` would turn any
``logger.error(...)`` (ours or a library's, e.g. httpx) into an event whose ``logentry``
holds free text: URLs with query strings, page data, tokens. Init disables log-to-event
and log-to-breadcrumb; ``scrub_event`` always drops ``logentry`` and breadcrumbs and
keeps ``message`` only when it fullmatches what :func:`report_unreachable_domains`
sends (job name, count, host names reduced to safe tokens). Fail closed.
"""

from __future__ import annotations

import os
import re
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
        logging_integration = import_module("sentry_sdk.integrations.logging")
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
        # No log records as events or breadcrumbs: their text is free-form.
        integrations=[
            logging_integration.LoggingIntegration(
                level=None, event_level=None, sentry_logs_level=None
            )
        ],
    )
    sdk.set_tag("job", job)
    return sdk


_HOST = r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?"
_SAFE_HOST = re.compile(_HOST)
# The only message this module sends (report_unreachable_domains).
_OWN_MESSAGE = re.compile(
    rf"[a-z0-9_]{{1,40}}: \d+ owned domain\(s\) unreachable: {_HOST}(?:, {_HOST})*"
)


def scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Drop request data, log text, frame locals and exception messages; keep the type."""
    for key in ("request", "extra", "logentry", "breadcrumbs"):
        event.pop(key, None)
    message = event.get("message")
    if not (isinstance(message, str) and _OWN_MESSAGE.fullmatch(message)):
        event.pop("message", None)
    for value in (event.get("exception") or {}).get("values") or []:
        if value.get("value"):
            value["value"] = ""
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return event


def report_exception(sdk: ModuleType | None, error: BaseException) -> None:
    """Send a failed run as one exception event."""
    if sdk is not None:
        sdk.capture_exception(error)
        sdk.flush(timeout=5)


def _safe_host(domain: object) -> str:
    text = str(domain).lower()
    return text if _SAFE_HOST.fullmatch(text) else "other"


def report_unreachable_domains(
    sdk: ModuleType | None, job: str, domains: list[str]
) -> None:
    """Send one ``warning`` for a partial capture: job, count and host names only."""
    if sdk is not None and domains:
        names = ", ".join(_safe_host(domain) for domain in domains)
        sdk.capture_message(
            f"{job}: {len(domains)} owned domain(s) unreachable: {names}",
            level="warning",
        )
        sdk.flush(timeout=5)
