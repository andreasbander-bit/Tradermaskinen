from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from src.providers import TwelveDataProvider


class TwelveDataMinuteProviderTests(unittest.TestCase):
    @patch("src.providers.requests.get")
    def test_get_1m_requests_utc_minute_bars(self, mock_get: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "values": [
                {
                    "datetime": "2026-08-03 13:30:00",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "10",
                }
            ]
        }
        mock_get.return_value = response

        start = datetime(2026, 8, 3, tzinfo=timezone.utc)
        end = datetime(2026, 8, 4, tzinfo=timezone.utc)
        bars = TwelveDataProvider(api_key="test-token").get_1m(
            "AAPL",
            start,
            end,
        )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["interval"], "1min")
        self.assertEqual(params["timezone"], "UTC")
        self.assertEqual(params["start_date"], "2026-08-03 00:00:00")
        self.assertEqual(params["end_date"], "2026-08-04 00:00:00")
        self.assertEqual(bars[0].timestamp, datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()