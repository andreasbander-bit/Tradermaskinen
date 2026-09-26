from __future__ import annotations

from datetime import datetime, timezone

from src.providers import TwelveDataProvider
from src.quality import validate_bars, print_quality_report


START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader — DQ-01 ===")
    print(f"Provider: Twelve Data")
    print(f"Symbol: AAPL")
    print(f"Period: {START.isoformat()} -> {END.isoformat()}")
    print()

    provider = TwelveDataProvider()

    bars = provider.get_1h(
        "AAPL",
        START,
        END,
    )

    print(f"Downloaded bars: {len(bars)}")

    report = validate_bars(bars)

    print_quality_report(report)


if __name__ == "__main__":
    main()
