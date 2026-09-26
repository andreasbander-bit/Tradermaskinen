from __future__ import annotations

from datetime import datetime, timezone

from src.compare import compare_bars, print_comparison
from src.providers import EODHDProvider, TwelveDataProvider, ProviderError
from src.quality import print_quality_report, validate_bars


START = datetime(2026, 8, 3, tzinfo=timezone.utc)
END = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _is_unavailable_entitlement(error: Exception) -> bool:
    return (
        isinstance(error, ProviderError)
        and "HTTP 403" in str(error)
    )


def _print_dq05_result(
    twelve_data_status: str,
    eodhd_status: str,
    comparison_executed: bool,
    reason: str | None,
) -> None:
    print("\n=== DQ-05 RESULT ===")
    print(f"Twelve Data status: {twelve_data_status}")
    print(f"EODHD status: {eodhd_status}")
    print(
        "Cross-provider comparison executed: "
        f"{'YES' if comparison_executed else 'NO'}"
    )
    if reason:
        print(f"Reason: {reason}")


def main() -> None:
    print("=== Always-on Trader Data Quality PoC ===")
    print(f"Period: {START.isoformat()} -> {END.isoformat()}")
    print()

    eodhd_bars = []
    twelve_bars = []
    eodhd_status = "FAILED"
    twelve_data_status = "FAILED"

    try:
        print("Fetching EODHD...")
        eodhd_bars = EODHDProvider().get_1h(
            "AAPL.US",
            START,
            END,
        )
        eodhd_status = "AVAILABLE" if eodhd_bars else "NO_DATA"
        print(f"EODHD: {eodhd_status} ({len(eodhd_bars)} bars)")
    except Exception as exc:
        if _is_unavailable_entitlement(exc):
            eodhd_status = "UNAVAILABLE_ENTITLEMENT"
            print(f"EODHD: {eodhd_status}")
        else:
            eodhd_status = "FAILED"
            print(f"EODHD: {eodhd_status} ({type(exc).__name__})")

    try:
        print("Fetching Twelve Data...")
        twelve_bars = TwelveDataProvider().get_1h(
            "AAPL",
            START,
            END,
        )
        print(f"Twelve Data: {len(twelve_bars)} bars")
        report = validate_bars(twelve_bars)
        print_quality_report(report)
        twelve_data_status = "PASS" if report.passed else "FAIL"
    except Exception as exc:
        twelve_data_status = "FAILED"
        print(f"Twelve Data: {twelve_data_status} ({type(exc).__name__})")

    comparison_executed = False
    reason = None
    if eodhd_bars and twelve_bars:
        try:
            result = compare_bars(eodhd_bars, twelve_bars)
            print_comparison(result)
            comparison_executed = True
        except Exception as exc:
            reason = f"Comparison failed ({type(exc).__name__})."
    else:
        reasons = []
        if eodhd_status == "UNAVAILABLE_ENTITLEMENT":
            reasons.append("EODHD intraday entitlement unavailable (HTTP 403).")
        elif not eodhd_bars:
            reasons.append(f"EODHD data unavailable ({eodhd_status}).")
        if not twelve_bars:
            reasons.append("Twelve Data returned no bars.")
        reason = " ".join(reasons)

    _print_dq05_result(
        twelve_data_status,
        eodhd_status,
        comparison_executed,
        reason,
    )


if __name__ == "__main__":
    main()