from __future__ import annotations

from datetime import datetime, timezone

from src.compare import compare_bars, print_comparison
from src.providers import EODHDProvider, TwelveDataProvider, ProviderError


START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def main() -> None:
    print("=== Always-on Trader Data Quality PoC ===")
    print(f"Period: {START.isoformat()} -> {END.isoformat()}")
    print()

    eodhd_bars = []
    twelve_bars = []

    try:
        print("Fetching EODHD...")
        eodhd_bars = EODHDProvider().get_1h(
            "AAPL.US",
            START,
            END,
        )
        print(f"EODHD: {len(eodhd_bars)} bars")
    except Exception as exc:
        print(f"EODHD: FAILED - {type(exc).__name__}: {exc}")

    try:
        print("Fetching Twelve Data...")
        twelve_bars = TwelveDataProvider().get_1h(
            "AAPL",
            START,
            END,
        )
        print(f"Twelve Data: {len(twelve_bars)} bars")
    except Exception as exc:
        print(f"Twelve Data: FAILED - {type(exc).__name__}: {exc}")

    if eodhd_bars and twelve_bars:
        result = compare_bars(eodhd_bars, twelve_bars)
        print_comparison(result)
    else:
        print()
        print("=== COMPARISON NOT RUN ===")
        print("Both providers must return data before")
        print("a cross-provider comparison can be performed.")


if __name__ == "__main__":
    main()