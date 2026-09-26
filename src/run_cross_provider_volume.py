from __future__ import annotations

from datetime import date, datetime, timezone

from src.completeness import expected_us_rth_1h_timestamps
from src.cross_provider_volume import build_dq08_result, print_dq08_result
from src.providers import EODHDProvider, ProviderError, TwelveDataProvider


SYMBOL = "AAPL"
START_DATE = date(2026, 8, 3)
END_DATE = date(2026, 8, 3)
START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _fetch_eodhd() -> tuple[str, list]:
    try:
        bars = EODHDProvider().get_1h("AAPL.US", START, END)
        return ("AVAILABLE" if bars else "NO_DATA", bars)
    except ProviderError as error:
        if "HTTP 403" in str(error):
            return "UNAVAILABLE_ENTITLEMENT", []
        return "UNAVAILABLE", []
    except Exception:
        return "UNAVAILABLE", []


def _fetch_twelve_data() -> tuple[str, list]:
    try:
        bars = TwelveDataProvider().get_1h(SYMBOL, START, END)
        return ("AVAILABLE" if bars else "NO_DATA", bars)
    except Exception:
        return "UNAVAILABLE", []


def main() -> None:
    print("=== Always-on Trader — DQ-08 ===")
    print("Test: Cross-provider canonical 1H RTH volume")
    print(f"Symbol: {SYMBOL}")
    print(f"Trading dates: {START_DATE} -> {END_DATE}")

    eodhd_status, eodhd_bars = _fetch_eodhd()
    twelve_data_status, twelve_data_bars = _fetch_twelve_data()
    result = build_dq08_result(
        eodhd_status,
        eodhd_bars,
        twelve_data_status,
        twelve_data_bars,
        expected_us_rth_1h_timestamps(START_DATE, END_DATE),
    )
    print_dq08_result(result)


if __name__ == "__main__":
    main()