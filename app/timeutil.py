"""Time helpers. Costa Rica uses a fixed UTC-6 offset (no DST).

All timestamps are stored as naive local strings 'YYYY-MM-DDTHH:MM', so
string comparison equals chronological comparison.
"""
import re
from datetime import date, datetime, timedelta, timezone

CR_TZ = timezone(timedelta(hours=-6))
DT_FMT = "%Y-%m-%dT%H:%M"
DATE_FMT = "%Y-%m-%d"

_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HM_RE = re.compile(r"^\d{2}:\d{2}$")


def now_local():
    """Current Costa Rica local time as a naive datetime (minute precision)."""
    return datetime.now(CR_TZ).replace(tzinfo=None, second=0, microsecond=0)


def fmt_dt(value):
    return value.strftime(DT_FMT)


def fmt_date(value):
    return value.strftime(DATE_FMT)


def parse_dt(value):
    """Parse 'YYYY-MM-DDTHH:MM' strictly; return datetime or None."""
    if not isinstance(value, str) or not _DT_RE.match(value):
        return None
    try:
        return datetime.strptime(value, DT_FMT)
    except ValueError:
        return None


def parse_date(value):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        return None


def parse_hm(value):
    """Parse 'HH:MM' into minutes since midnight; return int or None."""
    if not isinstance(value, str) or not _HM_RE.match(value):
        return None
    hours, minutes = int(value[:2]), int(value[3:])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def minutes_of_day(value):
    return value.hour * 60 + value.minute


def add_months(value, months):
    """Shift a datetime by a number of months (day clamped to 28)."""
    index = value.year * 12 + (value.month - 1) + months
    year, month = divmod(index, 12)
    return value.replace(year=year, month=month + 1, day=min(value.day, 28))


def week_monday(value):
    """Monday of the week that contains the given date."""
    if isinstance(value, datetime):
        value = value.date()
    return value - timedelta(days=value.weekday())


def overlaps(a_start, a_end, b_start, b_end):
    """Half-open interval overlap: [a_start, a_end) vs [b_start, b_end)."""
    return a_start < b_end and b_start < a_end


__all__ = [
    "CR_TZ", "DT_FMT", "DATE_FMT", "now_local", "fmt_dt", "fmt_date", "parse_dt",
    "parse_date", "parse_hm", "minutes_of_day", "add_months", "week_monday",
    "overlaps", "date", "datetime", "timedelta",
]
