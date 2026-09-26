from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.providers import MarketBar


@dataclass(frozen=True)
class QualityReport:
    provider: str
    symbol: str
    total_bars: int
    duplicate_timestamps: int
    out_of_order_bars: int
    invalid_ohlc_bars: int
    invalid_volume_bars: int
    non_utc_timestamps: int
    first_timestamp: str | None
    last_timestamp: str | None

    @property
    def passed(self) -> bool:
        return all(
            value == 0
            for value in (
                self.duplicate_timestamps,
                self.out_of_order_bars,
                self.invalid_ohlc_bars,
                self.invalid_volume_bars,
                self.non_utc_timestamps,
            )
        )


def validate_bars(bars: Iterable[MarketBar]) -> QualityReport:
    bars = list(bars)

    if not bars:
        raise ValueError("Cannot validate an empty dataset.")

    duplicate_timestamps = 0
    out_of_order_bars = 0
    invalid_ohlc_bars = 0
    invalid_volume_bars = 0
    non_utc_timestamps = 0

    seen_timestamps: set = set()
    previous_timestamp = None

    for bar in bars:
        timestamp = bar.timestamp

        if timestamp in seen_timestamps:
            duplicate_timestamps += 1
        else:
            seen_timestamps.add(timestamp)

        if previous_timestamp is not None and timestamp < previous_timestamp:
            out_of_order_bars += 1

        previous_timestamp = timestamp

        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            non_utc_timestamps += 1

        if not (
            bar.high >= bar.open
            and bar.high >= bar.close
            and bar.low <= bar.open
            and bar.low <= bar.close
            and bar.high >= bar.low
        ):
            invalid_ohlc_bars += 1

        if bar.volume < 0:
            invalid_volume_bars += 1

    return QualityReport(
        provider=bars[0].provider,
        symbol=bars[0].symbol,
        total_bars=len(bars),
        duplicate_timestamps=duplicate_timestamps,
        out_of_order_bars=out_of_order_bars,
        invalid_ohlc_bars=invalid_ohlc_bars,
        invalid_volume_bars=invalid_volume_bars,
        non_utc_timestamps=non_utc_timestamps,
        first_timestamp=bars[0].timestamp.isoformat(),
        last_timestamp=bars[-1].timestamp.isoformat(),
    )


def print_quality_report(report: QualityReport) -> None:
    print("\n=== DATA QUALITY REPORT ===")
    print(f"Provider:             {report.provider}")
    print(f"Symbol:               {report.symbol}")
    print(f"Total bars:           {report.total_bars}")
    print(f"Duplicate timestamps: {report.duplicate_timestamps}")
    print(f"Out of order:         {report.out_of_order_bars}")
    print(f"Invalid OHLC:         {report.invalid_ohlc_bars}")
    print(f"Invalid volume:       {report.invalid_volume_bars}")
    print(f"Non-UTC timestamps:   {report.non_utc_timestamps}")
    print(f"First timestamp:      {report.first_timestamp}")
    print(f"Last timestamp:       {report.last_timestamp}")
    print(f"STATUS:               {'PASS' if report.passed else 'FAIL'}")