from __future__ import annotations

from src.corporate_actions import (
    TwelveDataCorporateActionsProbe,
    print_corporate_action_report,
)
from src.providers import TwelveDataProvider


SYMBOL = "AAPL"


def main() -> None:
    print("=== Always-on Trader — DQ-10 ===")
    print("Test: Corporate actions and historical OHLCV behavior")
    print("Provider: Twelve Data")
    print(f"Symbol: {SYMBOL}")
    print("Canonical timeframe: 1H RTH")

    try:
        provider = TwelveDataProvider()
    except Exception as error:
        print(f"Provider status: UNAVAILABLE ({type(error).__name__})")
        return

    report = TwelveDataCorporateActionsProbe(provider).run(SYMBOL)
    print_corporate_action_report(report)


if __name__ == "__main__":
    main()