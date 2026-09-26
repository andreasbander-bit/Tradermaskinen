from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.providers import MarketBar


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CompletenessReport:
    provider: str
    symbol: str
    expected_bars: int
    actual_bars: int
    missing_bars: int
    unexpected_bars: int
    missing_timestamps: tuple[str, ...]
    unexpected_timestamps: tuple[str, ...]

    @property
    def completeness_pct(self) -> float:
        if self.expected_bars == 0:
            return 100.0

        return (
            (self.expected_bars - self.missing_bars)
            / self.expected_bars
            * 100.0
        )

    @property
    def passed(self) -> bool:
        return self.missing_bars == 0 and self.unexpected_bars == 0


def expected_us_rth_1h_timestamps(
    start_date: date,
    end_date: date,
) -> list[datetime]:
    """
    Generate expected hourly RTH timestamps.

    DQ-02 v1 scope:
    - Monday-Friday
    - normal US session
    - no exchange-holiday handling yet
    - no early-close handling yet
    """

    expected: list[datetime] = []
    current = start_date

    while current <= end_date:
        if current.weekday() < 5:
            session_start = datetime.combine(
                current,
                time(9, 30),
                tzinfo=NEW_YORK,
            )

            for hour_offset in range(7):
                timestamp = session_start + timedelta(hours=hour_offset)
                expected.append(
                    timestamp.astimezone(timezone.utc)
                )

        current += timedelta(days=1)

    return expected


def check_completeness(
    bars: list[MarketBar],
    expected_timestamps: list[datetime],
) -> CompletenessReport:
    if not bars:
        raise ValueError("Cannot check completeness of an empty dataset.")

    actual_timestamps = {bar.timestamp for bar in bars}
    expected_set = set(expected_timestamps)

    missing = sorted(expected_set - actual_timestamps)
    unexpected = sorted(actual_timestamps - expected_set)

    return CompletenessReport(
        provider=bars[0].provider,
        symbol=bars[0].symbol,
        expected_bars=len(expected_set),
        actual_bars=len(actual_timestamps),
        missing_bars=len(missing),
        unexpected_bars=len(unexpected),
        missing_timestamps=tuple(
            timestamp.isoformat()
            for timestamp in missing
        ),
        unexpected_timestamps=tuple(
            timestamp.isoformat()
            for timestamp in unexpected
        ),
    )


def print_completeness_report(
    report: CompletenessReport,
) -> None:
    print("\n=== DQ-02 COMPLETENESS REPORT ===")
    print(f"Provider:        {report.provider}")
    print(f"Symbol:          {report.symbol}")
    print(f"Expected bars:   {report.expected_bars}")
    print(f"Actual bars:     {report.actual_bars}")
    print(f"Missing bars:    {report.missing_bars}")
    print(f"Unexpected bars: {report.unexpected_bars}")
    print(f"Completeness:    {report.completeness_pct:.2f}%")

    if report.missing_timestamps:
        print("\nMissing timestamps:")
        for timestamp in report.missing_timestamps:
            print(f"  {timestamp}")

    if report.unexpected_timestamps:
        print("\nUnexpected timestamps:")
        for timestamp in report.unexpected_timestamps:
            print(f"  {timestamp}")

    print(
        f"\nSTATUS: {'PASS' if report.passed else 'FAIL'}"
    )