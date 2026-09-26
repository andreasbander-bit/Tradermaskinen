from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from io import StringIO
import unittest

from src.daily_validation import (
    CANONICAL_SERIES_POLICY,
    VOLUME_COMPOSITION_NOTE,
    aggregate_rth_1h_bars,
    build_dq06_result,
    compare_daily_bars,
    print_dq06_result,
)
from src.providers import MarketBar
from src.sessions import NEW_YORK


def make_bar(
    timestamp: datetime,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    provider: str = "Twelve Data",
) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        provider=provider,
    )


class DailyValidationTests(unittest.TestCase):
    def test_aggregation_uses_sorted_rth_bars_and_excludes_other_sessions(
        self,
    ) -> None:
        bars = [
            make_bar(datetime(2026, 8, 3, 20, 30, tzinfo=timezone.utc), 50, 51, 49, 50, 500),
            make_bar(datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc), 102, 105, 101, 104, 20),
            make_bar(datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc), 90, 91, 89, 90, 900),
            make_bar(datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc), 100, 103, 99, 102, 10),
            make_bar(datetime(2026, 8, 3, 15, 30, tzinfo=timezone.utc), 104, 106, 102, 105, 30),
        ]

        aggregated = aggregate_rth_1h_bars(bars)

        self.assertEqual(len(aggregated), 1)
        daily = aggregated[0]
        self.assertEqual(daily.timestamp.astimezone(NEW_YORK).date().isoformat(), "2026-08-03")
        self.assertEqual(daily.open, 100)
        self.assertEqual(daily.high, 106)
        self.assertEqual(daily.low, 99)
        self.assertEqual(daily.close, 105)
        self.assertEqual(daily.volume, 60)

    def test_compare_reports_date_and_value_differences(self) -> None:
        aggregate = make_bar(
            datetime(2026, 8, 3, 4, tzinfo=timezone.utc),
            100,
            105,
            99,
            103,
            600,
            "Twelve Data 1H aggregate",
        )
        daily = make_bar(
            datetime(2026, 8, 3, 4, tzinfo=timezone.utc),
            100,
            104,
            99,
            102,
            590,
        )
        missing = make_bar(
            datetime(2026, 8, 4, 4, tzinfo=timezone.utc),
            100,
            100,
            100,
            100,
            10,
            "Twelve Data 1H aggregate",
        )
        unexpected = make_bar(
            datetime(2026, 8, 5, 4, tzinfo=timezone.utc),
            100,
            100,
            100,
            100,
            10,
        )

        report = compare_daily_bars([aggregate, missing], [daily, unexpected])

        self.assertEqual(report.matching_dates, ("2026-08-03",))
        self.assertEqual(report.missing_dates, ("2026-08-04",))
        self.assertEqual(report.unexpected_dates, ("2026-08-05",))
        self.assertEqual(
            [(mismatch.field, mismatch.absolute_difference) for mismatch in report.ohlc_mismatches],
            [("high", 1), ("close", 1)],
        )
        self.assertEqual(len(report.volume_mismatches), 1)
        self.assertEqual(report.max_ohlc_absolute_difference, 1)
        self.assertEqual(report.max_volume_absolute_difference, 10)
        self.assertEqual(report.parity_status, "DIFFERENCE_DETECTED")

    def test_no_matching_dates_is_inconclusive(self) -> None:
        aggregate = make_bar(
            datetime(2026, 8, 3, 4, tzinfo=timezone.utc), 100, 100, 100, 100, 10
        )
        daily = make_bar(
            datetime(2026, 8, 4, 4, tzinfo=timezone.utc), 100, 100, 100, 100, 10
        )

        report = compare_daily_bars([aggregate], [daily])

        self.assertEqual(report.parity_status, "INCONCLUSIVE")
        self.assertEqual(report.matching_dates, ())

    def test_integrity_passes_independently_of_provider_daily_difference(
        self,
    ) -> None:
        minute_bars = []
        for minute_offset in range(60):
            if minute_offset == 0:
                values = (100, 102, 99, 101)
            elif minute_offset == 59:
                values = (101, 104, 98, 103)
            else:
                values = (101, 101, 100, 101)
            minute_bars.append(
                make_bar(
                    datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
                    + timedelta(minutes=minute_offset),
                    *values,
                    1,
                )
            )
        canonical_hourly = [
            make_bar(
                datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc),
                100,
                104,
                98,
                103,
                60,
            )
        ]
        derived_daily = aggregate_rth_1h_bars(canonical_hourly)
        native_daily = [
            make_bar(
                derived_daily[0].timestamp,
                99,
                104,
                98,
                102,
                29,
            )
        ]

        result = build_dq06_result(
            minute_bars,
            canonical_hourly,
            derived_daily,
            native_daily,
        )

        self.assertEqual(result.aggregation_integrity.status, "PASS")
        self.assertEqual(
            result.aggregation_integrity.minute_to_hour_status,
            "PASS",
        )
        self.assertEqual(
            result.aggregation_integrity.hour_to_daily_status,
            "PASS",
        )
        self.assertEqual(
            result.provider_daily_parity.parity_status,
            "DIFFERENCE_DETECTED",
        )

        output = StringIO()
        with redirect_stdout(output):
            print_dq06_result(result)
        rendered = output.getvalue()
        self.assertIn("=== AGGREGATION_INTEGRITY ===", rendered)
        self.assertIn("=== PROVIDER_DAILY_PARITY ===", rendered)
        self.assertIn("DIFFERENCE_DETECTED", rendered)
        self.assertIn(CANONICAL_SERIES_POLICY, rendered)
        self.assertIn(VOLUME_COMPOSITION_NOTE, rendered)

    def test_incomplete_minute_coverage_does_not_pass_integrity(self) -> None:
        complete_minute_bars = []
        for minute_offset in range(60):
            if minute_offset == 0:
                values = (100, 102, 99, 101)
            elif minute_offset == 59:
                values = (101, 104, 98, 103)
            else:
                values = (101, 101, 100, 101)
            complete_minute_bars.append(
                make_bar(
                    datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
                    + timedelta(minutes=minute_offset),
                    *values,
                    1,
                )
            )
        canonical_hourly = [
            make_bar(
                datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc),
                100,
                104,
                98,
                103,
                60,
            )
        ]
        derived_daily = aggregate_rth_1h_bars(canonical_hourly)

        result = build_dq06_result(
            complete_minute_bars[:-1],
            canonical_hourly,
            derived_daily,
            derived_daily,
        )

        self.assertEqual(result.aggregation_integrity.status, "FAIL")
        self.assertEqual(
            result.aggregation_integrity.minute_to_hour_status,
            "FAIL",
        )


if __name__ == "__main__":
    unittest.main()