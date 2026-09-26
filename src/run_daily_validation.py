from __future__ import annotations

from datetime import date, datetime, timezone

from src.daily_validation import (
    aggregate_rth_1h_bars,
    build_dq06_result,
    print_dq06_result,
)
from src.providers import ProviderError, TwelveDataProvider


SYMBOL = "AAPL"
START_DATE = date(2026, 8, 3)
END_DATE = date(2026, 8, 3)
START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader — DQ-06 ===")
    print("Test: Daily vs aggregated 1H validation")
    print("Provider: Twelve Data")
    print(f"Symbol: {SYMBOL}")
    print(f"Trading dates: {START_DATE} -> {END_DATE}")
    print()

    provider = TwelveDataProvider()
    try:
        hourly_bars = provider.get_1h(SYMBOL, START, END)
    except Exception as error:
        print(f"1H source unavailable ({type(error).__name__}).")
        return
    try:
        daily_bars = provider.get_daily(SYMBOL, START_DATE, END_DATE)
    except Exception as error:
        print(f"Daily reference unavailable ({type(error).__name__}).")
        return
    try:
        minute_bars = provider.get_1m(SYMBOL, START, END)
    except Exception as error:
        minute_bars = []
        print(f"1M integrity source unavailable ({type(error).__name__}).")
    aggregated_bars = aggregate_rth_1h_bars(hourly_bars)

    print(f"Downloaded 1H bars: {len(hourly_bars)}")
    print(f"Downloaded 1M integrity bars: {len(minute_bars)}")
    print(f"Aggregated RTH dates: {len(aggregated_bars)}")
    print(f"Downloaded daily bars: {len(daily_bars)}")

    result = build_dq06_result(
        minute_bars,
        hourly_bars,
        aggregated_bars,
        daily_bars,
    )
    print_dq06_result(result)


if __name__ == "__main__":
    main()