from __future__ import annotations

import argparse

from src.api_reliability import (
    DEFAULT_BURST_REQUEST_COUNT,
    MAX_BURST_REQUEST_COUNT,
    NORMAL_SYMBOLS,
    print_dq13_report,
    run_api_reliability,
)
from src.providers import ProviderError, TwelveDataProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DQ-13 API reliability test.")
    parser.add_argument(
        "--burst-requests",
        type=int,
        default=DEFAULT_BURST_REQUEST_COUNT,
        help=f"bounded identical requests (0-{MAX_BURST_REQUEST_COUNT}; default {DEFAULT_BURST_REQUEST_COUNT})",
    )
    parser.add_argument(
        "--burst-only",
        action="store_true",
        help="skip the normal request stage and run only the bounded burst",
    )
    args = parser.parse_args()

    try:
        provider = TwelveDataProvider()
    except ProviderError:
        print("DQ-13 status: NOT_EXECUTED (Twelve Data API key is not configured).")
        return

    report = run_api_reliability(
        provider,
        normal_symbols=() if args.burst_only else NORMAL_SYMBOLS,
        burst_request_count=args.burst_requests,
    )
    print_dq13_report(report)


if __name__ == "__main__":
    main()