from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from src.providers import MarketBar
from src.sessions import classify_us_timestamp


OHLC_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class OHLCDifference:
    timestamp: str
    field: str
    eodhd_value: float
    twelve_data_value: float
    absolute_difference: float
    relative_difference_pct: float


@dataclass(frozen=True)
class OHLCFieldSummary:
    field: str
    max_absolute_difference: float | None
    max_relative_difference_pct: float | None


@dataclass(frozen=True)
class CrossProviderOHLCReport:
    comparison_status: str
    matching_timestamps: tuple[str, ...]
    missing_from_eodhd: tuple[str, ...]
    missing_from_twelve_data: tuple[str, ...]
    unexpected_eodhd: tuple[str, ...]
    unexpected_twelve_data: tuple[str, ...]
    duplicate_eodhd: tuple[str, ...]
    duplicate_twelve_data: tuple[str, ...]
    ohlc_mismatches: tuple[OHLCDifference, ...]
    field_summaries: tuple[OHLCFieldSummary, ...]


@dataclass(frozen=True)
class ProviderFetchStatus:
    status: str
    bars: tuple[MarketBar, ...]


@dataclass(frozen=True)
class DQ07Result:
    eodhd: ProviderFetchStatus
    twelve_data: ProviderFetchStatus
    comparison: CrossProviderOHLCReport | None
    comparison_status: str
    reason: str | None


def _utc_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Provider timestamps must be timezone-aware.")
    return timestamp.astimezone(timezone.utc)


def _rth_timestamp_map(
    bars: Iterable[MarketBar],
) -> tuple[dict[datetime, MarketBar], set[datetime], set[datetime]]:
    by_timestamp: dict[datetime, MarketBar] = {}
    unexpected: set[datetime] = set()
    duplicates: set[datetime] = set()

    for bar in bars:
        timestamp = _utc_timestamp(bar.timestamp)
        if not classify_us_timestamp(timestamp).is_rth:
            unexpected.add(timestamp)
            continue
        if timestamp in by_timestamp:
            duplicates.add(timestamp)
            continue
        by_timestamp[timestamp] = bar

    return by_timestamp, unexpected, duplicates


def compare_rth_1h_bars(
    eodhd_bars: Iterable[MarketBar],
    twelve_data_bars: Iterable[MarketBar],
    expected_timestamps: Iterable[datetime],
) -> CrossProviderOHLCReport:
    expected = {_utc_timestamp(timestamp) for timestamp in expected_timestamps}
    eodhd, eodhd_unexpected, duplicate_eodhd = _rth_timestamp_map(eodhd_bars)
    twelve_data, twelve_unexpected, duplicate_twelve_data = _rth_timestamp_map(
        twelve_data_bars
    )

    eodhd_timestamps = set(eodhd)
    twelve_data_timestamps = set(twelve_data)
    matching = sorted(expected & eodhd_timestamps & twelve_data_timestamps)
    missing_from_eodhd = expected - eodhd_timestamps
    missing_from_twelve_data = expected - twelve_data_timestamps
    eodhd_unexpected |= eodhd_timestamps - expected
    twelve_unexpected |= twelve_data_timestamps - expected

    differences_by_field: dict[str, list[OHLCDifference]] = {
        field: [] for field in OHLC_FIELDS
    }
    mismatches: list[OHLCDifference] = []

    for timestamp in matching:
        eodhd_bar = eodhd[timestamp]
        twelve_bar = twelve_data[timestamp]
        for field in OHLC_FIELDS:
            eodhd_value = getattr(eodhd_bar, field)
            twelve_value = getattr(twelve_bar, field)
            absolute_difference = abs(eodhd_value - twelve_value)
            denominator = max(abs(eodhd_value), abs(twelve_value))
            relative_difference_pct = (
                0.0
                if denominator == 0
                else absolute_difference / denominator * 100.0
            )
            difference = OHLCDifference(
                timestamp=timestamp.isoformat(),
                field=field,
                eodhd_value=eodhd_value,
                twelve_data_value=twelve_value,
                absolute_difference=absolute_difference,
                relative_difference_pct=relative_difference_pct,
            )
            differences_by_field[field].append(difference)
            if absolute_difference != 0:
                mismatches.append(difference)

    field_summaries = tuple(
        OHLCFieldSummary(
            field=field,
            max_absolute_difference=(
                max(item.absolute_difference for item in field_differences)
                if field_differences
                else None
            ),
            max_relative_difference_pct=(
                max(item.relative_difference_pct for item in field_differences)
                if field_differences
                else None
            ),
        )
        for field, field_differences in differences_by_field.items()
    )

    has_differences = any(
        (
            missing_from_eodhd,
            missing_from_twelve_data,
            eodhd_unexpected,
            twelve_unexpected,
            duplicate_eodhd,
            duplicate_twelve_data,
            mismatches,
        )
    )
    if not expected:
        comparison_status = "INCONCLUSIVE"
    elif has_differences:
        comparison_status = "DIFFERENCE_DETECTED"
    else:
        comparison_status = "PASS"

    return CrossProviderOHLCReport(
        comparison_status=comparison_status,
        matching_timestamps=tuple(item.isoformat() for item in matching),
        missing_from_eodhd=tuple(
            item.isoformat() for item in sorted(missing_from_eodhd)
        ),
        missing_from_twelve_data=tuple(
            item.isoformat() for item in sorted(missing_from_twelve_data)
        ),
        unexpected_eodhd=tuple(item.isoformat() for item in sorted(eodhd_unexpected)),
        unexpected_twelve_data=tuple(
            item.isoformat() for item in sorted(twelve_unexpected)
        ),
        duplicate_eodhd=tuple(item.isoformat() for item in sorted(duplicate_eodhd)),
        duplicate_twelve_data=tuple(
            item.isoformat() for item in sorted(duplicate_twelve_data)
        ),
        ohlc_mismatches=tuple(mismatches),
        field_summaries=field_summaries,
    )


def build_dq07_result(
    eodhd_status: str,
    eodhd_bars: Iterable[MarketBar],
    twelve_data_status: str,
    twelve_data_bars: Iterable[MarketBar],
    expected_timestamps: Iterable[datetime],
) -> DQ07Result:
    eodhd_fetch = ProviderFetchStatus(eodhd_status, tuple(eodhd_bars))
    twelve_fetch = ProviderFetchStatus(twelve_data_status, tuple(twelve_data_bars))

    if eodhd_status != "AVAILABLE" or twelve_data_status != "AVAILABLE":
        reasons = []
        if eodhd_status == "UNAVAILABLE_ENTITLEMENT":
            reasons.append("EODHD intraday entitlement unavailable (HTTP 403).")
        elif eodhd_status != "AVAILABLE":
            reasons.append(f"EODHD unavailable ({eodhd_status}).")
        if twelve_data_status != "AVAILABLE":
            reasons.append(f"Twelve Data unavailable ({twelve_data_status}).")
        return DQ07Result(
            eodhd=eodhd_fetch,
            twelve_data=twelve_fetch,
            comparison=None,
            comparison_status="NOT_EXECUTED",
            reason=" ".join(reasons),
        )

    comparison = compare_rth_1h_bars(
        eodhd_fetch.bars,
        twelve_fetch.bars,
        expected_timestamps,
    )
    return DQ07Result(
        eodhd=eodhd_fetch,
        twelve_data=twelve_fetch,
        comparison=comparison,
        comparison_status=comparison.comparison_status,
        reason=None,
    )


def print_dq07_result(result: DQ07Result) -> None:
    print("\n=== DQ-07 CROSS-PROVIDER 1H OHLC ===")
    print(f"EODHD status: {result.eodhd.status}")
    print(f"Twelve Data status: {result.twelve_data.status}")
    print(f"Comparison status: {result.comparison_status}")
    if result.reason:
        print(f"Reason: {result.reason}")
    if result.comparison is None:
        return

    report = result.comparison
    print(f"Matching timestamps: {len(report.matching_timestamps)}")
    print("Missing from EODHD:", ", ".join(report.missing_from_eodhd) or "none")
    print(
        "Missing from Twelve Data:",
        ", ".join(report.missing_from_twelve_data) or "none",
    )
    print("Unexpected EODHD:", ", ".join(report.unexpected_eodhd) or "none")
    print(
        "Unexpected Twelve Data:",
        ", ".join(report.unexpected_twelve_data) or "none",
    )
    print("Duplicate EODHD:", ", ".join(report.duplicate_eodhd) or "none")
    print(
        "Duplicate Twelve Data:",
        ", ".join(report.duplicate_twelve_data) or "none",
    )
    print(f"OHLC mismatches: {len(report.ohlc_mismatches)}")
    for mismatch in report.ohlc_mismatches:
        print(
            f"  {mismatch.timestamp} {mismatch.field}: "
            f"EODHD={mismatch.eodhd_value} "
            f"TwelveData={mismatch.twelve_data_value} "
            f"abs={mismatch.absolute_difference:.8f} "
            f"rel={mismatch.relative_difference_pct:.8f}%"
        )
    for summary in report.field_summaries:
        absolute = (
            "n/a"
            if summary.max_absolute_difference is None
            else f"{summary.max_absolute_difference:.8f}"
        )
        relative = (
            "n/a"
            if summary.max_relative_difference_pct is None
            else f"{summary.max_relative_difference_pct:.8f}%"
        )
        print(
            f"Max {summary.field.upper()} absolute difference: {absolute}; "
            f"relative difference: {relative}"
        )