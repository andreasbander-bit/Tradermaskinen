from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionCheck:
    timestamp: datetime
    session_date: str
    session_type: str
    is_rth: bool
    is_tradable: bool


def classify_us_timestamp(timestamp: datetime) -> SessionCheck:
    """
    Classify a timestamp according to the US equity session.

    v1 scope:
    - America/New_York timezone
    - RTH: 09:30-16:00
    - Pre-market: 04:00-09:30
    - After-hours: 16:00-20:00
    - Overnight/closed: everything else

    This is a session classifier, not yet a full exchange calendar.
    Holidays and early closes are handled later.
    """

    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware.")

    local = timestamp.astimezone(NEW_YORK)
    local_time = local.time()

    if time(9, 30) <= local_time < time(16, 0):
        session_type = "RTH"
        is_rth = True
        is_tradable = True

    elif time(4, 0) <= local_time < time(9, 30):
        session_type = "PRE_MARKET"
        is_rth = False
        is_tradable = False

    elif time(16, 0) <= local_time < time(20, 0):
        session_type = "AFTER_HOURS"
        is_rth = False
        is_tradable = False

    else:
        session_type = "OVERNIGHT_CLOSED"
        is_rth = False
        is_tradable = False

    return SessionCheck(
        timestamp=timestamp,
        session_date=local.date().isoformat(),
        session_type=session_type,
        is_rth=is_rth,
        is_tradable=is_tradable,
    )


def validate_rth_bars(bars) -> dict:
    """
    Validate that all supplied bars belong to RTH.

    Returns a simple report suitable for DQ-04.
    """

    if not bars:
        raise ValueError("Cannot validate an empty bar dataset.")

    checks = [
        classify_us_timestamp(bar.timestamp)
        for bar in bars
    ]

    non_rth = [
        check
        for check in checks
        if not check.is_rth
    ]

    return {
        "total_bars": len(bars),
        "rth_bars": sum(check.is_rth for check in checks),
        "non_rth_bars": len(non_rth),
        "non_rth_timestamps": [
            check.timestamp.isoformat()
            for check in non_rth
        ],
        "status": "PASS" if not non_rth else "FAIL",
    }


def print_session_report(report: dict) -> None:
    print("\n=== DQ-04 SESSION INTEGRITY REPORT ===")
    print(f"Total bars:       {report['total_bars']}")
    print(f"RTH bars:         {report['rth_bars']}")
    print(f"Non-RTH bars:     {report['non_rth_bars']}")

    if report["non_rth_timestamps"]:
        print("\nNon-RTH timestamps:")
        for timestamp in report["non_rth_timestamps"]:
            print(f"  {timestamp}")

    print(f"\nSTATUS: {report['status']}")