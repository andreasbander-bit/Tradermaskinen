from __future__ import annotations

from src.instrument_mapping import TwelveDataInstrumentProbe, print_dq11_report
from src.providers import TwelveDataProvider


def main() -> None:
    print("=== Always-on Trader — DQ-11 ===")
    print("Test: Symbol / instrument mapping validation")
    print("Provider: Twelve Data")
    print("Universe: AAPL, TSLA, NVDA, Investor A/B, Saab A/B, Volvo A/B")

    try:
        provider = TwelveDataProvider()
    except Exception as error:
        print(f"Provider status: UNAVAILABLE ({type(error).__name__})")
        return

    report = TwelveDataInstrumentProbe(provider).run()
    print_dq11_report(report)


if __name__ == "__main__":
    main()