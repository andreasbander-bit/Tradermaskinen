from __future__ import annotations

from datetime import datetime, timezone

from src.historical_depth import (
    TwelveDataHistoricalProbe,
    print_historical_depth_report,
)
from src.providers import TwelveDataProvider


SYMBOL = "AAPL"


def main() -> None:
    print("=== Always-on Trader — DQ-09 ===")
    print("Test: Actual historical depth")
    print("Provider: Twelve Data")
    print(f"Symbol: {SYMBOL}")
    print("Canonical timeframe: 1H RTH")

    probe = TwelveDataHistoricalProbe(TwelveDataProvider())
    report = probe.run(SYMBOL, datetime.now(timezone.utc))
    print_historical_depth_report(report)


if __name__ == "__main__":
    main()