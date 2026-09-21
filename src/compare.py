from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.providers import MarketBar


@dataclass(frozen=True)
class ComparisonResult:
    provider_a: str
    provider_b: str

    bars_a: int
    bars_b: int

    common_timestamps: int
    only_a: int
    only_b: int

    max_open_diff: float | None
    max_high_diff: float | None
    max_low_diff: float | None
    max_close_diff: float | None
    max_volume_diff_pct: float | None

    first_timestamp_a: str | None
    last_timestamp_a: str | None
    first_timestamp_b: str | None
    last_timestamp_b: str | None


def _by_timestamp(
    bars: Iterable[MarketBar],
) -> dict:
    return {bar.timestamp: bar for bar in bars}


def _max_abs_diff(
    values: list[tuple[float, float]],
) -> float | None:
    if not values:
        return None

    return max(abs(a - b) for a, b in values)


def _max_volume_diff_pct(
    values: list[tuple[float, float]],
) -> float | None:
    differences: list[float] = []

    for volume_a, volume_b in values:
        if volume_a == 0 and volume_b == 0:
            differences.append(0.0)
            continue

        if volume_a == 0 or volume_b == 0:
            differences.append(100.0)
            continue

        differences.append(
            abs(volume_a - volume_b)
            / max(abs(volume_a), abs(volume_b))
            * 100.0
        )

    return max(differences) if differences else None


def compare_bars(
    bars_a: Iterable[MarketBar],
    bars_b: Iterable[MarketBar],
) -> ComparisonResult:
    bars_a = list(bars_a)
    bars_b = list(bars_b)

    if not bars_a:
        raise ValueError("Provider A returned no bars.")

    if not bars_b:
        raise ValueError("Provider B returned no bars.")

    map_a = _by_timestamp(bars_a)
    map_b = _by_timestamp(bars_b)

    timestamps_a = set(map_a)
    timestamps_b = set(map_b)

    common = sorted(timestamps_a & timestamps_b)

    open_values = [
        (map_a[t].open, map_b[t].open)
        for t in common
    ]

    high_values = [
        (map_a[t].high, map_b[t].high)
        for t in common
    ]

    low_values = [
        (map_a[t].low, map_b[t].low)
        for t in common
    ]

    close_values = [
        (map_a[t].close, map_b[t].close)
        for t in common
    ]

    volume_values = [
        (map_a[t].volume, map_b[t].volume)
        for t in common
    ]

    timestamps_a_sorted = sorted(timestamps_a)
    timestamps_b_sorted = sorted(timestamps_b)

    return ComparisonResult(
        provider_a=bars_a[0].provider,
        provider_b=bars_b[0].provider,
        bars_a=len(bars_a),
        bars_b=len(bars_b),
        common_timestamps=len(common),
        only_a=len(timestamps_a - timestamps_b),
        only_b=len(timestamps_b - timestamps_a),
        max_open_diff=_max_abs_diff(open_values),
        max_high_diff=_max_abs_diff(high_values),
        max_low_diff=_max_abs_diff(low_values),
        max_close_diff=_max_abs_diff(close_values),
        max_volume_diff_pct=_max_volume_diff_pct(volume_values),
        first_timestamp_a=timestamps_a_sorted[0].isoformat(),
        last_timestamp_a=timestamps_a_sorted[-1].isoformat(),
        first_timestamp_b=timestamps_b_sorted[0].isoformat(),
        last_timestamp_b=timestamps_b_sorted[-1].isoformat(),
    )


def print_comparison(result: ComparisonResult) -> None:
    print("\n=== DATA QUALITY COMPARISON ===")
    print(f"Provider A: {result.provider_a}")
    print(f"Provider B: {result.provider_b}")
    print()

    print(f"Bars A:              {result.bars_a}")
    print(f"Bars B:              {result.bars_b}")
    print(f"Common timestamps:   {result.common_timestamps}")
    print(f"Only in A:           {result.only_a}")
    print(f"Only in B:           {result.only_b}")
    print()

    print(f"Max OPEN difference:  {result.max_open_diff}")
    print(f"Max HIGH difference:  {result.max_high_diff}")
    print(f"Max LOW difference:   {result.max_low_diff}")
    print(f"Max CLOSE difference: {result.max_close_diff}")
    print(f"Max volume difference: {result.max_volume_diff_pct}%")
    print()

    print(f"A first timestamp: {result.first_timestamp_a}")
    print(f"A last timestamp:  {result.last_timestamp_a}")
    print(f"B first timestamp: {result.first_timestamp_b}")
    print(f"B last timestamp:  {result.last_timestamp_b}")