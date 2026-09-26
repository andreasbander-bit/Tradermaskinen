from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from typing import Iterable

from src.cross_provider_validation import (
    ProviderFetchStatus,
    _rth_timestamp_map,
    _utc_timestamp,
)
from src.providers import MarketBar


@dataclass(frozen=True)
class VolumeMismatch:
    timestamp: str
    eodhd_volume: float
    twelve_data_volume: float
    absolute_difference: float
    relative_difference_pct: float


@dataclass(frozen=True)
class CrossProviderVolumeReport:
    comparison_status: str
    matching_timestamps: tuple[str, ...]
    missing_from_eodhd: tuple[str, ...]
    missing_from_twelve_data: tuple[str, ...]
    unexpected_eodhd: tuple[str, ...]
    unexpected_twelve_data: tuple[str, ...]
    duplicate_eodhd: tuple[str, ...]
    duplicate_twelve_data: tuple[str, ...]
    volume_mismatches: tuple[VolumeMismatch, ...]
    max_absolute_volume_difference: float | None
    max_relative_volume_difference_pct: float | None
    aggregate_volume_difference: float | None


@dataclass(frozen=True)
class DQ08Result:
    eodhd: ProviderFetchStatus
    twelve_data: ProviderFetchStatus
    comparison: CrossProviderVolumeReport | None
    comparison_status: str
    reason: str | None


def compare_rth_1h_volume(
    eodhd_bars: Iterable[MarketBar],
    twelve_data_bars: Iterable[MarketBar],
    expected_timestamps: Iterable,
) -> CrossProviderVolumeReport:
    expected = {_utc_timestamp(timestamp) for timestamp in expected_timestamps}
    eodhd, unexpected_eodhd, duplicate_eodhd = _rth_timestamp_map(eodhd_bars)
    twelve_data, unexpected_twelve_data, duplicate_twelve_data = (
        _rth_timestamp_map(twelve_data_bars)
    )

    eodhd_timestamps = set(eodhd)
    twelve_data_timestamps = set(twelve_data)
    matching = sorted(expected & eodhd_timestamps & twelve_data_timestamps)
    missing_from_eodhd = expected - eodhd_timestamps
    missing_from_twelve_data = expected - twelve_data_timestamps
    unexpected_eodhd |= eodhd_timestamps - expected
    unexpected_twelve_data |= twelve_data_timestamps - expected

    differences: list[VolumeMismatch] = []
    mismatches: list[VolumeMismatch] = []
    for timestamp in matching:
        eodhd_volume = eodhd[timestamp].volume
        twelve_data_volume = twelve_data[timestamp].volume
        absolute_difference = abs(eodhd_volume - twelve_data_volume)
        denominator = max(abs(eodhd_volume), abs(twelve_data_volume))
        relative_difference_pct = (
            0.0
            if denominator == 0
            else absolute_difference / denominator * 100.0
        )
        difference = VolumeMismatch(
            timestamp=timestamp.isoformat(),
            eodhd_volume=eodhd_volume,
            twelve_data_volume=twelve_data_volume,
            absolute_difference=absolute_difference,
            relative_difference_pct=relative_difference_pct,
        )
        differences.append(difference)
        if absolute_difference:
            mismatches.append(difference)

    has_differences = any(
        (
            missing_from_eodhd,
            missing_from_twelve_data,
            unexpected_eodhd,
            unexpected_twelve_data,
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

    eodhd_total = fsum(eodhd[timestamp].volume for timestamp in matching)
    twelve_data_total = fsum(twelve_data[timestamp].volume for timestamp in matching)
    return CrossProviderVolumeReport(
        comparison_status=comparison_status,
        matching_timestamps=tuple(timestamp.isoformat() for timestamp in matching),
        missing_from_eodhd=tuple(
            timestamp.isoformat() for timestamp in sorted(missing_from_eodhd)
        ),
        missing_from_twelve_data=tuple(
            timestamp.isoformat() for timestamp in sorted(missing_from_twelve_data)
        ),
        unexpected_eodhd=tuple(
            timestamp.isoformat() for timestamp in sorted(unexpected_eodhd)
        ),
        unexpected_twelve_data=tuple(
            timestamp.isoformat() for timestamp in sorted(unexpected_twelve_data)
        ),
        duplicate_eodhd=tuple(
            timestamp.isoformat() for timestamp in sorted(duplicate_eodhd)
        ),
        duplicate_twelve_data=tuple(
            timestamp.isoformat() for timestamp in sorted(duplicate_twelve_data)
        ),
        volume_mismatches=tuple(mismatches),
        max_absolute_volume_difference=(
            max(item.absolute_difference for item in differences)
            if matching
            else None
        ),
        max_relative_volume_difference_pct=(
            max(item.relative_difference_pct for item in differences)
            if matching
            else None
        ),
        aggregate_volume_difference=(
            eodhd_total - twelve_data_total if matching else None
        ),
    )


def build_dq08_result(
    eodhd_status: str,
    eodhd_bars: Iterable[MarketBar],
    twelve_data_status: str,
    twelve_data_bars: Iterable[MarketBar],
    expected_timestamps: Iterable,
) -> DQ08Result:
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
        return DQ08Result(
            eodhd=eodhd_fetch,
            twelve_data=twelve_fetch,
            comparison=None,
            comparison_status="NOT_EXECUTED",
            reason=" ".join(reasons),
        )

    comparison = compare_rth_1h_volume(
        eodhd_fetch.bars,
        twelve_fetch.bars,
        expected_timestamps,
    )
    return DQ08Result(
        eodhd=eodhd_fetch,
        twelve_data=twelve_fetch,
        comparison=comparison,
        comparison_status=comparison.comparison_status,
        reason=None,
    )


def print_dq08_result(result: DQ08Result) -> None:
    print("\n=== DQ-08 CROSS-PROVIDER 1H RTH VOLUME ===")
    print(f"EODHD status: {result.eodhd.status}")
    print(f"Twelve Data status: {result.twelve_data.status}")
    print(f"Comparison status: {result.comparison_status}")
    if result.reason:
        print(f"Reason: {result.reason}")
    if result.comparison is None:
        return

    report = result.comparison
    print("Matching timestamps:", ", ".join(report.matching_timestamps) or "none")
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
    print(f"Volume mismatches: {len(report.volume_mismatches)}")
    for mismatch in report.volume_mismatches:
        print(
            f"  {mismatch.timestamp}: EODHD={mismatch.eodhd_volume} "
            f"TwelveData={mismatch.twelve_data_volume} "
            f"abs={mismatch.absolute_difference:.6f} "
            f"rel={mismatch.relative_difference_pct:.8f}%"
        )
    maximum_absolute = (
        "n/a"
        if report.max_absolute_volume_difference is None
        else f"{report.max_absolute_volume_difference:.6f}"
    )
    maximum_relative = (
        "n/a"
        if report.max_relative_volume_difference_pct is None
        else f"{report.max_relative_volume_difference_pct:.8f}%"
    )
    aggregate_difference = (
        "n/a"
        if report.aggregate_volume_difference is None
        else f"{report.aggregate_volume_difference:.6f}"
    )
    print(f"Maximum absolute volume difference: {maximum_absolute}")
    print(f"Maximum relative volume difference: {maximum_relative}")
    print(
        "Aggregate volume difference (EODHD - Twelve Data): "
        f"{aggregate_difference}"
    )