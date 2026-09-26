from __future__ import annotations

from datetime import datetime, timezone

from src.providers import TwelveDataProvider
from src.sessions import validate_rth_bars, print_session_report


START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader — DQ-04 ===")
    print("Test: US RTH session integrity")
    print("Provider: Twelve Data")
    print("Symbol: AAPL")
    print()

    provider = TwelveDataProvider()

    bars = provider.get_1h(
        "AAPL",
        START,
        END,
    )

    print(f"Downloaded bars: {len(bars)}")

    report = validate_rth_bars(bars)

    print_session_report(report)


if __name__ == "__main__":
    main()