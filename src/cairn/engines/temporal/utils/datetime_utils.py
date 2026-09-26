from datetime import datetime, timezone


def utc_now() -> datetime:
    """Returns the current UTC datetime with timezone information."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Ensures a datetime is timezone-aware and in UTC.
    If the datetime is naive (no timezone), assumes it's in UTC.
    If the datetime has a different timezone, converts it to UTC.
    Returns None if input is None.
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        # If datetime is naive, assume it's UTC
        return dt.replace(tzinfo=timezone.utc)
    elif dt.tzinfo != timezone.utc:
        # If datetime has a different timezone, convert to UTC
        return dt.astimezone(timezone.utc)

    return dt


def convert_datetimes_to_strings(obj):
    if isinstance(obj, dict):
        return {k: convert_datetimes_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_datetimes_to_strings(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_datetimes_to_strings(item) for item in obj)
    elif isinstance(obj, datetime):
        # Normalize to UTC before serializing. The resulting ISO strings are
        # compared lexicographically by drivers that store datetimes as strings
        # (e.g. FalkorDB), which is only correct when all offsets are identical.
        utc_dt = ensure_utc(obj)
        assert utc_dt is not None
        return utc_dt.isoformat()
    else:
        return obj
