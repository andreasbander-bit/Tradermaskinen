from __future__ import annotations

from src.providers import TwelveDataProvider
from src.repeat_download import print_dq12_report, run_repeat_downloads


def main() -> None:
    print("=== Always-on Trader — DQ-12 ===")
    print("Test: Repeat-download stability")
    print("Provider: Twelve Data")
    print("Period: 2026-08-03T00:00:00+00:00 -> 2026-08-04T00:00:00+00:00")
    print("Interval: 1H; canonical RTH only")

    try:
        provider = TwelveDataProvider()
    except Exception as error:
        print(f"Provider status: PROVIDER_LIMITATION ({type(error).__name__})")
        return

    report = run_repeat_downloads(provider)
    print_dq12_report(report)


if __name__ == "__main__":
    main()