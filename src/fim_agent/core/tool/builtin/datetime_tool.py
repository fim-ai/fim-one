"""Built-in tool for getting the current date and time."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..base import BaseTool


class DateTimeTool(BaseTool):
    """Return the current date, time, and timezone information.

    Useful when the agent needs to know the current time — e.g. generating
    time-stamped reports, checking whether information is up-to-date, or
    computing relative durations.
    """

    @property
    def name(self) -> str:
        return "datetime"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "Get the current date and time. "
            "Optionally specify a timezone (e.g. 'UTC', 'Asia/Shanghai', 'America/New_York'). "
            "Defaults to UTC."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA timezone name, e.g. 'UTC', 'Asia/Shanghai', "
                        "'America/New_York'. Defaults to 'UTC' if omitted."
                    ),
                }
            },
        }

    async def run(self, **kwargs: Any) -> str:
        tz_name: str = kwargs.get("timezone", "UTC") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            return f"[Error] Unknown timezone: '{tz_name}'. Use an IANA name like 'UTC' or 'Asia/Shanghai'."

        now = datetime.now(tz)
        return (
            f"Current date/time:\n"
            f"  ISO 8601 : {now.isoformat()}\n"
            f"  Date     : {now.strftime('%Y-%m-%d')}\n"
            f"  Time     : {now.strftime('%H:%M:%S')}\n"
            f"  Weekday  : {now.strftime('%A')}\n"
            f"  Timezone : {tz_name} (UTC{now.strftime('%z')})"
        )
