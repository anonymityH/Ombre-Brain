"""Shared chronology rules for historical-memory navigation.

``event_time`` describes when the remembered event happened. ``created``
describes when OB wrote the bucket. Explicit historical navigation prefers the
former and only falls back to the latter when no event timestamp exists.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from utils import get_tzinfo


def _parse_aware(value: object, *, upper_date: bool = False) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty datetime")
    if raw[-1:].lower() == "z":
        raw = raw[:-1] + "+00:00"
    if len(raw) == 10:
        day = datetime.fromisoformat(raw).date()
        parsed = datetime.combine(day, time.max if upper_date else time.min)
    else:
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=get_tzinfo())
    return parsed.astimezone(timezone.utc)


def parse_date_range(
    date_from: str = "",
    date_to: str = "",
) -> tuple[datetime | None, datetime | None]:
    lower = _parse_aware(date_from) if str(date_from or "").strip() else None
    upper = (
        _parse_aware(date_to, upper_date=len(str(date_to).strip()) == 10)
        if str(date_to or "").strip()
        else None
    )
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("date_from 不能晚于 date_to。")
    return lower, upper


def bucket_time_interval(
    bucket: dict[str, Any],
) -> tuple[datetime, datetime, str] | None:
    """Return the effective interval and its basis: ``event`` or ``created``.

    A present-but-invalid event timestamp fails closed instead of silently
    relocating a historical memory to its later import date.
    """

    metadata = bucket.get("metadata", {}) or {}
    event_raw = str(metadata.get("event_time") or "").strip()
    if event_raw:
        try:
            start = _parse_aware(event_raw)
        except (TypeError, ValueError, OverflowError):
            return None
        end_raw = str(metadata.get("event_time_end") or "").strip()
        if end_raw:
            try:
                end = _parse_aware(end_raw)
            except (TypeError, ValueError, OverflowError):
                end = start
        else:
            end = start
        if end < start:
            end = start
        return start, end, "event"

    created_raw = str(metadata.get("created") or "").strip()
    if not created_raw:
        return None
    try:
        created = _parse_aware(created_raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return created, created, "created"


def bucket_in_date_range(
    bucket: dict[str, Any],
    lower: datetime | None,
    upper: datetime | None,
) -> bool:
    if lower is None and upper is None:
        return True
    interval = bucket_time_interval(bucket)
    if interval is None:
        return False
    start, end, _basis = interval
    # Inclusive interval overlap: an event spanning midnight belongs to both
    # dates instead of disappearing from one side of the boundary.
    if lower is not None and end < lower:
        return False
    if upper is not None and start > upper:
        return False
    return True


def chronology_label(metadata: dict[str, Any]) -> tuple[str, str]:
    event_time = str(metadata.get("event_time") or "").strip()
    if event_time:
        return "event_time", event_time
    created = str(metadata.get("created") or "").strip()
    return ("created", created) if created else ("", "")
