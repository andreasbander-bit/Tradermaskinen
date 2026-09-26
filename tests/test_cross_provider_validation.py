from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import unittest
from unittest.mock import patch

from src.cross_provider_validation import build_dq07_result
from src.providers import MarketBar, ProviderError
from src.run_cross_provider_ohlc import main


def make_bar(
    timestamp: datetime,
    open_price: float = 100.0,
    high: float = 102.0,
    low: float = 99.0,
    close: float = 101.0,
) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timestamp=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        provider="test",
    )


class CrossProviderValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
        self.second = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)
        self.expected = [self.first, self.second]

    def test_identical_provider_data_passes(self) -> None:
        eodhd_bars = [make_bar(self.first), make_bar(self.second)]
        twelve_data_bars = [make_bar(self.first), make_bar(self.second)]

        result = build_dq07_result(
            "AVAILABLE",
            eodhd_bars,
            "AVAILABLE",
            twelve_data_bars,
            self.expected,
        )

        self.assertEqual(result.comparison_status, "PASS")
        self.assertEqual(
            result.comparison.matching_timestamps,
            tuple(timestamp.isoformat() for timestamp in self.expected),
        )
        self.assertEqual(result.comparison.ohlc_mismatches, ())

    def test_known_ohlc_difference_is_detected(self) -> None:
        eodhd_bars = [make_bar(self.first), make_bar(self.second)]
        twelve_data_bars = [
            make_bar(self.first, close=101.5),
            make_bar(self.second),
        ]

        result = build_dq07_result(
            "AVAILABLE",
            eodhd_bars,
            "AVAILABLE",
            twelve_data_bars,
            self.expected,
        )

        self.assertEqual(result.comparison_status, "DIFFERENCE_DETECTED")
        self.assertEqual(len(result.comparison.ohlc_mismatches), 1)
        mismatch = result.comparison.ohlc_mismatches[0]
        self.assertEqual(mismatch.field, "close")
        self.assertEqual(mismatch.absolute_difference, 0.5)
        close_summary = next(
            item
            for item in result.comparison.field_summaries
            if item.field == "close"
        )
        self.assertEqual(close_summary.max_absolute_difference, 0.5)
        self.assertEqual(
            close_summary.max_relative_difference_pct,
            0.5 / 101.5 * 100,
        )

    def test_missing_timestamp_is_detected(self) -> None:
        eodhd_bars = [make_bar(self.first), make_bar(self.second)]
        twelve_data_bars = [make_bar(self.first)]

        result = build_dq07_result(
            "AVAILABLE",
            eodhd_bars,
            "AVAILABLE",
            twelve_data_bars,
            self.expected,
        )

        self.assertEqual(result.comparison_status, "DIFFERENCE_DETECTED")
        self.assertEqual(
            result.comparison.missing_from_twelve_data,
            (self.second.isoformat(),),
        )

    @patch("src.run_cross_provider_ohlc.EODHDProvider")
    @patch("src.run_cross_provider_ohlc.TwelveDataProvider")
    def test_eodhd_403_is_unavailable_and_comparison_not_executed(
        self,
        twelve_provider: patch,
        eodhd_provider: patch,
    ) -> None:
        eodhd_provider.return_value.get_1h.side_effect = ProviderError(
            "EODHD request failed with HTTP 403."
        )
        twelve_provider.return_value.get_1h.return_value = [
            make_bar(self.first),
            make_bar(self.second),
        ]
        output = StringIO()

        with redirect_stdout(output):
            main()

        rendered = output.getvalue()
        self.assertIn("EODHD status: UNAVAILABLE_ENTITLEMENT", rendered)
        self.assertIn("Twelve Data status: AVAILABLE", rendered)
        self.assertIn("Comparison status: NOT_EXECUTED", rendered)
        self.assertIn("EODHD intraday entitlement unavailable", rendered)


if __name__ == "__main__":
    unittest.main()