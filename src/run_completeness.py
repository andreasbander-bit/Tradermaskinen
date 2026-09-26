from __future__ import annotations

from datetime import date, datetime, timezone

from src.completeness import (
    expected_us_rth_1h_timestamps,
    check_completeness,
    print_completeness_report,
)
from src.providers import TwelveDataProvider


START_DATE = date(2026, 8, 3)
END_DATE = date(2026, 8, 3)

START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader — DQ-02 ===")
    print("Test: US 1H completeness")
    print("Provider: Twelve Data")
    print("Symbol: AAPL")
    print(f"Trading date: {START_DATE}")
    print()

    provider = TwelveDataProvider()

    bars = provider.get_1h(
        "AAPL",
        START,
        END,
    )

    expected = expected_us_rth_1h_timestamps(
        START_DATE,
        END_DATE,
    )

    report = check_completeness(
        bars,
        expected,
    )

    print_completeness_report(report)


if __name__ == "__main__":
    main()