from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import unittest
from unittest.mock import patch

from src.providers import MarketBar, ProviderError
from src.run_comparison import main


class ComparisonRunnerTests(unittest.TestCase):
    def test_eodhd_403_keeps_twelve_data_validation_and_skips_comparison(
        self,
    ) -> None:
        twelve_bars = [
            MarketBar(
                symbol="AAPL",
                timestamp=datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc),
                open=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=1000.0,
                provider="Twelve Data",
            )
        ]
        output = StringIO()

        with (
            patch("src.run_comparison.EODHDProvider") as eodhd_provider,
            patch("src.run_comparison.TwelveDataProvider") as twelve_provider,
            patch("src.run_comparison.compare_bars") as compare_bars,
            redirect_stdout(output),
        ):
            eodhd_provider.return_value.get_1h.side_effect = ProviderError(
                "EODHD request failed with HTTP 403."
            )
            twelve_provider.return_value.get_1h.return_value = twelve_bars

            main()

        report = output.getvalue()
        self.assertIn("STATUS:               PASS", report)
        self.assertIn("Twelve Data status: PASS", report)
        self.assertIn("EODHD status: UNAVAILABLE_ENTITLEMENT", report)
        self.assertIn("Cross-provider comparison executed: NO", report)
        self.assertIn("EODHD intraday entitlement unavailable (HTTP 403)", report)
        compare_bars.assert_not_called()


if __name__ == "__main__":
    unittest.main()