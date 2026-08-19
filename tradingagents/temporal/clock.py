"""UTC timestamp helpers and an explicit virtual clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def format_timestamp(value: datetime) -> str:
    """Return a canonical, UTC ISO-8601 timestamp."""
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str | datetime) -> datetime:
    """Parse a timestamp and require an explicit timezone."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class VirtualClock:
    """The single time boundary used by a temporal scenario."""

    as_of: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", parse_timestamp(self.as_of))
