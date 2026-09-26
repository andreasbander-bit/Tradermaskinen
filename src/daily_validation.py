from __future__ import annotations

"""DQ-06 research-series policy.

Canonical intraday research data is 1H RTH. 4H and Daily research bars are
derived deterministically from canonical 1H RTH data. Native provider Daily
bars are retained only as an external validation/reference series and are not
used as the canonical research series.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import fsum, isclose
from typing import Iterable

from src.providers import MarketBar
from src.sessions import NEW_YORK, classify_us_timestamp


CANONICAL_SERIES_POLICY = (
    "Canonical intraday research data is 1H RTH. "
    "4H and Daily research bars are derived deterministically from canonical "
    "1H RTH data. Native provider Daily bars are retained only as an external "
    "validation/reference series and are not used as the canonical research "
    "series."
)


OHLC_FIELDS = ("open", "high", "low", "close")
OHLCV_FIELDS = (*OHLC_FIELDS, "volume")
VOLUME_COMPOSITION_NOTE = (
    "Daily volume composition is unknown because extended-hours data is "
    "unauthorized."
)


@dataclass(frozen=True)
class ValueMismatch:
    session_date: str
    field: str
    aggregated_value: float
    daily_value: float
    absolute_difference: float
    relative_difference_pct: float


@dataclass(frozen=True)
class DailyValidationReport:
    matching_dates: tuple[str, ...]
    missing_dates: tuple[str, ...]
    unexpected_dates: tuple[str, ...]
    ohlc_differences: tuple[ValueMismatch, ...]
    volume_differences: tuple[ValueMismatch, ...]
    ohlc_mismatches: tuple[ValueMismatch, ...]
    volume_mismatches: tuple[ValueMismatch, ...]
    max_ohlc_absolute_difference: float | None
    max_ohlc_relative_difference_pct: float | None
    max_volume_absolute_difference: float | None
    max_volume_relative_difference_pct: float | None
    parity_status: str


@dataclass(frozen=True)
class AggregationIntegrityReport:
    status: str
    minute_to_hour_status: str
    hour_to_daily_status: str
    minute_to_hour_mismatches: int
    hour_to_daily_mismatches: int


@dataclass(frozen=True)
class DQ06Result:
    aggregation_integrity: AggregationIntegrityReport
    provider_daily_parity: DailyValidationReport


def _bar_session_date(bar: MarketBar) -> str:
    if bar.timestamp.tzinfo is None:
        raise ValueError("Bar timestamps must be timezone-aware.")
    return bar.timestamp.astimezone(NEW_YORK).date().isoformat()


def aggregate_rth_1h_bars(
    bars: Iterable[MarketBar],
) -> list[MarketBar]:
    grouped: dict[str, list[MarketBar]] = {}
    seen_timestamps: set[datetime] = set()

    for bar in bars:
        session = classify_us_timestamp(bar.timestamp)
        if not session.is_rth:
            continue
        if bar.timestamp in seen_timestamps:
            raise ValueError(
                f"Duplicate RTH timestamp: {bar.timestamp.isoformat()}"
            )
        seen_timestamps.add(bar.timestamp)
        grouped.setdefault(session.session_date, []).append(bar)

    aggregated: list[MarketBar] = []
    for session_date, session_bars in sorted(grouped.items()):
        ordered = sorted(session_bars, key=lambda bar: bar.timestamp)
        day = date.fromisoformat(session_date)
        timestamp = datetime.combine(
            day,
            time.min,
            tzinfo=NEW_YORK,
        ).astimezone(timezone.utc)

        aggregated.append(
            MarketBar(
                symbol=ordered[0].symbol,
                timestamp=timestamp,
                open=ordered[0].open,
                high=max(bar.high for bar in ordered),
                low=min(bar.low for bar in ordered),
                close=ordered[-1].close,
                volume=fsum(bar.volume for bar in ordered),
                provider="Twelve Data 1H aggregate",
            )
        )

    return aggregated


def aggregate_rth_1m_bars(
    bars: Iterable[MarketBar],
) -> list[MarketBar]:
    grouped: dict[datetime, list[MarketBar]] = {}
    seen_timestamps: set[datetime] = set()

    for bar in bars:
        session = classify_us_timestamp(bar.timestamp)
        if not session.is_rth:
            continue
        if bar.timestamp in seen_timestamps:
            raise ValueError(
                f"Duplicate RTH timestamp: {bar.timestamp.isoformat()}"
            )
        seen_timestamps.add(bar.timestamp)

        local = bar.timestamp.astimezone(NEW_YORK)
        minutes_from_open = local.hour * 60 + local.minute - (9 * 60 + 30)
        bucket_offset = minutes_from_open // 60
        bucket_start = datetime.combine(
            local.date(),
            time(9, 30),
            tzinfo=NEW_YORK,
        ) + timedelta(hours=bucket_offset)
        grouped.setdefault(bucket_start, []).append(bar)

    aggregated: list[MarketBar] = []
    for bucket_start, bucket_bars in sorted(grouped.items()):
        ordered = sorted(bucket_bars, key=lambda bar: bar.timestamp)
        aggregated.append(
            MarketBar(
                symbol=ordered[0].symbol,
                timestamp=bucket_start.astimezone(timezone.utc),
                open=ordered[0].open,
                high=max(bar.high for bar in ordered),
                low=min(bar.low for bar in ordered),
                close=ordered[-1].close,
                volume=fsum(bar.volume for bar in ordered),
                provider="Twelve Data 1M aggregate",
            )
        )

    return aggregated


def _series_mismatch_count(
    expected: Iterable[MarketBar],
    actual: Iterable[MarketBar],
) -> int:
    expected_bars = list(expected)
    actual_bars = list(actual)
    expected_by_timestamp = {bar.timestamp: bar for bar in expected_bars}
    actual_by_timestamp = {bar.timestamp: bar for bar in actual_bars}

    mismatches = (
        len(expected_bars) - len(expected_by_timestamp)
        + len(actual_bars) - len(actual_by_timestamp)
        + len(set(expected_by_timestamp) ^ set(actual_by_timestamp))
    )
    for timestamp in set(expected_by_timestamp) & set(actual_by_timestamp):
        expected_bar = expected_by_timestamp[timestamp]
        actual_bar = actual_by_timestamp[timestamp]
        mismatches += sum(
            not isclose(
                getattr(expected_bar, field),
                getattr(actual_bar, field),
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            for field in OHLCV_FIELDS
        )
    return mismatches


def validate_aggregation_integrity(
    minute_bars: Iterable[MarketBar],
    canonical_hourly_bars: Iterable[MarketBar],
    derived_daily_bars: Iterable[MarketBar],
) -> AggregationIntegrityReport:
    minute_bars = list(minute_bars)
    canonical_hourly_bars = list(canonical_hourly_bars)
    derived_daily_bars = list(derived_daily_bars)
    canonical_rth_hourly = [
        bar
        for bar in canonical_hourly_bars
        if classify_us_timestamp(bar.timestamp).is_rth
    ]

    if not minute_bars or not canonical_rth_hourly:
        minute_to_hour_status = "INCONCLUSIVE"
        minute_to_hour_mismatches = 0
    else:
        minute_hourly = aggregate_rth_1m_bars(minute_bars)
        minute_to_hour_mismatches = _series_mismatch_count(
            canonical_rth_hourly,
            minute_hourly,
        )
        expected_minute_timestamps: set[datetime] = set()
        for hourly_bar in canonical_rth_hourly:
            local_start = hourly_bar.timestamp.astimezone(NEW_YORK)
            minutes_until_close = 16 * 60 - (
                local_start.hour * 60 + local_start.minute
            )
            bucket_minutes = min(60, minutes_until_close)
            expected_minute_timestamps.update(
                hourly_bar.timestamp + timedelta(minutes=offset)
                for offset in range(max(0, bucket_minutes))
            )
        actual_minute_timestamps = {
            bar.timestamp
            for bar in minute_bars
            if classify_us_timestamp(bar.timestamp).is_rth
        }
        minute_to_hour_mismatches += len(
            expected_minute_timestamps - actual_minute_timestamps
        ) + len(actual_minute_timestamps - expected_minute_timestamps)
        minute_to_hour_status = (
            "PASS" if minute_to_hour_mismatches == 0 else "FAIL"
        )

    if not canonical_rth_hourly or not derived_daily_bars:
        hour_to_daily_status = "INCONCLUSIVE"
        hour_to_daily_mismatches = 0
    else:
        rederived_daily = aggregate_rth_1h_bars(canonical_rth_hourly)
        hour_to_daily_mismatches = _series_mismatch_count(
            derived_daily_bars,
            rederived_daily,
        )
        if minute_bars:
            minute_hourly = aggregate_rth_1m_bars(minute_bars)
            minute_derived_daily = aggregate_rth_1h_bars(minute_hourly)
            hour_to_daily_mismatches += _series_mismatch_count(
                rederived_daily,
                minute_derived_daily,
            )
        hour_to_daily_status = (
            "PASS" if hour_to_daily_mismatches == 0 else "FAIL"
        )

    stage_statuses = (minute_to_hour_status, hour_to_daily_status)
    if "FAIL" in stage_statuses:
        status = "FAIL"
    elif "INCONCLUSIVE" in stage_statuses:
        status = "INCONCLUSIVE"
    else:
        status = "PASS"

    return AggregationIntegrityReport(
        status=status,
        minute_to_hour_status=minute_to_hour_status,
        hour_to_daily_status=hour_to_daily_status,
        minute_to_hour_mismatches=minute_to_hour_mismatches,
        hour_to_daily_mismatches=hour_to_daily_mismatches,
    )


def _bars_by_session_date(
    bars: Iterable[MarketBar],
) -> dict[str, MarketBar]:
    by_date: dict[str, MarketBar] = {}
    for bar in bars:
        session_date = _bar_session_date(bar)
        if session_date in by_date:
            raise ValueError(f"Multiple daily bars for {session_date}.")
        by_date[session_date] = bar
    return by_date


def _difference(
    session_date: str,
    field: str,
    aggregated_value: float,
    daily_value: float,
) -> ValueMismatch:
    absolute_difference = abs(aggregated_value - daily_value)
    denominator = max(abs(aggregated_value), abs(daily_value))
    relative_difference_pct = (
        0.0
        if denominator == 0
        else absolute_difference / denominator * 100.0
    )
    return ValueMismatch(
        session_date=session_date,
        field=field,
        aggregated_value=aggregated_value,
        daily_value=daily_value,
        absolute_difference=absolute_difference,
        relative_difference_pct=relative_difference_pct,
    )


def _exceeds_tolerance(
    difference: ValueMismatch,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> bool:
    scale = max(
        abs(difference.aggregated_value),
        abs(difference.daily_value),
    )
    return difference.absolute_difference > max(
        absolute_tolerance,
        relative_tolerance * scale,
    )


def compare_daily_bars(
    aggregated_bars: Iterable[MarketBar],
    daily_bars: Iterable[MarketBar],
    *,
    ohlc_absolute_tolerance: float = 0.01,
    ohlc_relative_tolerance: float = 0.0,
    volume_absolute_tolerance: float = 0.0,
    volume_relative_tolerance: float = 0.0,
) -> DailyValidationReport:
    aggregated_by_date = _bars_by_session_date(aggregated_bars)
    daily_by_date = _bars_by_session_date(daily_bars)

    aggregated_dates = set(aggregated_by_date)
    daily_dates = set(daily_by_date)
    matching_dates = sorted(aggregated_dates & daily_dates)
    missing_dates = sorted(aggregated_dates - daily_dates)
    unexpected_dates = sorted(daily_dates - aggregated_dates)

    ohlc_differences: list[ValueMismatch] = []
    volume_differences: list[ValueMismatch] = []
    ohlc_mismatches: list[ValueMismatch] = []
    volume_mismatches: list[ValueMismatch] = []

    for session_date in matching_dates:
        aggregated = aggregated_by_date[session_date]
        daily = daily_by_date[session_date]

        for field in OHLC_FIELDS:
            difference = _difference(
                session_date,
                field,
                getattr(aggregated, field),
                getattr(daily, field),
            )
            ohlc_differences.append(difference)
            if _exceeds_tolerance(
                difference,
                ohlc_absolute_tolerance,
                ohlc_relative_tolerance,
            ):
                ohlc_mismatches.append(difference)

        volume_difference = _difference(
            session_date,
            "volume",
            aggregated.volume,
            daily.volume,
        )
        volume_differences.append(volume_difference)
        if _exceeds_tolerance(
            volume_difference,
            volume_absolute_tolerance,
            volume_relative_tolerance,
        ):
            volume_mismatches.append(volume_difference)

    if not matching_dates:
        parity_status = "INCONCLUSIVE"
    elif (
        missing_dates
        or unexpected_dates
        or ohlc_mismatches
        or volume_mismatches
    ):
        parity_status = "DIFFERENCE_DETECTED"
    else:
        parity_status = "MATCH"

    def maximum(
        differences: list[ValueMismatch],
        attribute: str,
    ) -> float | None:
        if not differences:
            return None
        return max(getattr(difference, attribute) for difference in differences)

    return DailyValidationReport(
        matching_dates=tuple(matching_dates),
        missing_dates=tuple(missing_dates),
        unexpected_dates=tuple(unexpected_dates),
        ohlc_differences=tuple(ohlc_differences),
        volume_differences=tuple(volume_differences),
        ohlc_mismatches=tuple(ohlc_mismatches),
        volume_mismatches=tuple(volume_mismatches),
        max_ohlc_absolute_difference=maximum(
            ohlc_differences,
            "absolute_difference",
        ),
        max_ohlc_relative_difference_pct=maximum(
            ohlc_differences,
            "relative_difference_pct",
        ),
        max_volume_absolute_difference=maximum(
            volume_differences,
            "absolute_difference",
        ),
        max_volume_relative_difference_pct=maximum(
            volume_differences,
            "relative_difference_pct",
        ),
        parity_status=parity_status,
    )


def build_dq06_result(
    minute_bars: Iterable[MarketBar],
    canonical_hourly_bars: Iterable[MarketBar],
    derived_daily_bars: Iterable[MarketBar],
    provider_daily_bars: Iterable[MarketBar],
) -> DQ06Result:
    canonical_hourly_bars = list(canonical_hourly_bars)
    derived_daily_bars = list(derived_daily_bars)
    provider_daily_bars = list(provider_daily_bars)
    return DQ06Result(
        aggregation_integrity=validate_aggregation_integrity(
            minute_bars,
            canonical_hourly_bars,
            derived_daily_bars,
        ),
        provider_daily_parity=compare_daily_bars(
            derived_daily_bars,
            provider_daily_bars,
        ),
    )


def print_dq06_result(result: DQ06Result) -> None:
    def display_dates(dates: tuple[str, ...]) -> str:
        return ", ".join(dates) if dates else "none"

    def display_number(value: float | None, suffix: str = "") -> str:
        return "n/a" if value is None else f"{value:.6f}{suffix}"

    integrity = result.aggregation_integrity
    parity = result.provider_daily_parity
    print("\n=== AGGREGATION_INTEGRITY ===")
    print(f"Status: {integrity.status}")
    print(f"1M -> canonical 1H: {integrity.minute_to_hour_status}")
    print(f"canonical 1H -> derived Daily: {integrity.hour_to_daily_status}")
    print(CANONICAL_SERIES_POLICY)

    print("\n=== PROVIDER_DAILY_PARITY ===")
    print(f"Status: {parity.parity_status}")
    print("Native provider Daily is reference-only; it is not canonical research data.")
    print(f"Matching dates:   {display_dates(parity.matching_dates)}")
    print(f"Missing dates:    {display_dates(parity.missing_dates)}")
    print(f"Unexpected dates: {display_dates(parity.unexpected_dates)}")
    print("OHLC differences:")
    for difference in parity.ohlc_differences:
        classification = (
            "DIFFERENCE_DETECTED"
            if difference in parity.ohlc_mismatches
            else "MATCH"
        )
        print(
            f"  {difference.session_date} {difference.field}: "
            f"derived={difference.aggregated_value} "
            f"native={difference.daily_value} "
            f"abs={difference.absolute_difference:.6f} "
            f"rel={difference.relative_difference_pct:.8f}% "
            f"{classification}"
        )
    print("Volume differences:")
    for difference in parity.volume_differences:
        print(
            f"  {difference.session_date}: "
            f"derived={difference.aggregated_value} "
            f"native={difference.daily_value} "
            f"abs={difference.absolute_difference:.6f} "
            f"rel={difference.relative_difference_pct:.8f}%"
        )
    print(
        "Max OHLC absolute difference: "
        f"{display_number(parity.max_ohlc_absolute_difference)}"
    )
    print(
        "Max OHLC relative difference: "
        f"{display_number(parity.max_ohlc_relative_difference_pct, '%')}"
    )
    print(
        "Max volume absolute difference: "
        f"{display_number(parity.max_volume_absolute_difference)}"
    )
    print(
        "Max volume relative difference: "
        f"{display_number(parity.max_volume_relative_difference_pct, '%')}"
    )
    print(VOLUME_COMPOSITION_NOTE)