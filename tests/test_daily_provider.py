from datetime import date, datetime, timezone
import unittest
from unittest.mock import Mock, patch

import requests

from src.providers import ProviderError, TwelveDataProvider
from src.sessions import NEW_YORK


class TwelveDataDailyProviderTests(unittest.TestCase):
    @patch("src.providers.requests.get")
    def test_get_daily_requests_exchange_timezone_and_normalizes_date(
        self,
        mock_get: Mock,
    ) -> None:
        response = Mock()
        response.json.return_value = {
            "values": [
                {
                    "datetime": "2026-08-03",
                    "open": "100",
                    "high": "105",
                    "low": "98",
                    "close": "103",
                    "volume": "12345",
                }
            ]
        }
        mock_get.return_value = response

        bars = TwelveDataProvider(api_key="test-token").get_daily(
            "AAPL",
            date(2026, 8, 3),
            date(2026, 8, 3),
        )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["interval"], "1day")
        self.assertEqual(params["timezone"], "America/New_York")
        self.assertEqual(params["start_date"], "2026-08-03 00:00:00")
        self.assertEqual(params["end_date"], "2026-08-04 00:00:00")
        self.assertEqual(len(bars), 1)
        self.assertEqual(
            bars[0].timestamp.astimezone(NEW_YORK).date(),
            date(2026, 8, 3),
        )
        self.assertEqual(
            bars[0].timestamp,
            datetime(2026, 8, 3, 4, tzinfo=timezone.utc),
        )

    @patch("src.providers.requests.get")
    def test_get_daily_redacts_authenticated_url_on_http_error(
        self,
        mock_get: Mock,
    ) -> None:
        token = "test-token"
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError(
            "400 Client Error for url: "
            "https://api.twelvedata.com/time_series?apikey=test-token"
        )
        mock_get.return_value = response

        with self.assertRaises(ProviderError) as error:
            TwelveDataProvider(api_key=token).get_daily(
                "AAPL",
                date(2026, 8, 3),
                date(2026, 8, 3),
            )

        self.assertNotIn(token, str(error.exception))
        self.assertNotIn("https://", str(error.exception))

    @patch("src.providers.requests.get")
    def test_get_daily_redacts_api_error_message(
        self,
        mock_get: Mock,
    ) -> None:
        response = Mock()
        response.json.return_value = {
            "status": "error",
            "code": 401,
            "message": "bad key at https://api.invalid/?apikey=test-token",
        }
        mock_get.return_value = response

        with self.assertRaises(ProviderError) as error:
            TwelveDataProvider(api_key="test-token").get_daily(
                "AAPL",
                date(2026, 8, 3),
                date(2026, 8, 3),
            )

        self.assertEqual(
            str(error.exception),
            "Twelve Data daily request failed with API code 401.",
        )


if __name__ == "__main__":
    unittest.main()