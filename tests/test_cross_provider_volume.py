from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import unittest
from unittest.mock import patch

from src.cross_provider_volume import build_dq08_result
from src.providers import MarketBar, ProviderError
from src.run_cross_provider_volume import main


def make_bar(
    timestamp: datetime,
    volume: float = 1000.0,
    open_price: float = 100.0,
) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timestamp=timestamp,
        open=open_price,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=volume,
        provider="test",
    )


class CrossProviderVolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc)
        self.second = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)
        self.expected = [self.first, self.second]

    def compare(self, eodhd_bars, twelve_data_bars):
        return build_dq08_result(
            "AVAILABLE",
            eodhd_bars,
            "AVAILABLE",
            twelve_data_bars,
            self.expected,
        )

    def test_identical_volume_passes_without_comparing_ohlc(self) -> None:
        eodhd_bars = [
            make_bar(self.first, open_price=100),
            make_bar(self.second, open_price=101),
        ]
        twelve_data_bars = [
            make_bar(self.first, open_price=200),
            make_bar(self.second, open_price=201),
        ]

        result = self.compare(eodhd_bars, twelve_data_bars)

        self.assertEqual(result.comparison_status, "PASS")
        self.assertEqual(result.comparison.volume_mismatches, ())
        self.assertEqual(result.comparison.aggregate_volume_difference, 0.0)

    def test_known_volume_difference_is_detected(self) -> None:
        result = self.compare(
            [make_bar(self.first, 1000), make_bar(self.second, 2000)],
            [make_bar(self.first, 900), make_bar(self.second, 2000)],
        )

        report = result.comparison
        self.assertEqual(result.comparison_status, "DIFFERENCE_DETECTED")
        self.assertEqual(len(report.volume_mismatches), 1)
        self.assertEqual(report.max_absolute_volume_difference, 100)
        self.assertEqual(report.max_relative_volume_difference_pct, 10.0)
        self.assertEqual(report.aggregate_volume_difference, 100.0)

    def test_missing_timestamp_is_detected(self) -> None:
        result = self.compare(
            [make_bar(self.first), make_bar(self.second)],
            [make_bar(self.first)],
        )

        self.assertEqual(result.comparison_status, "DIFFERENCE_DETECTED")
        self.assertEqual(
            result.comparison.missing_from_twelve_data,
            (self.second.isoformat(),),
        )

    @patch("src.run_cross_provider_volume.EODHDProvider")
    @patch("src.run_cross_provider_volume.TwelveDataProvider")
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