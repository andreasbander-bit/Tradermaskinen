from datetime import date, datetime, time, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

import requests

from src.providers import EODHDProvider, ProviderError


class EODHDProviderTests(unittest.TestCase):
    @patch("src.providers.requests.get")
    def test_get_1h_sends_numeric_utc_timestamps(self, mock_get: Mock) -> None:
        response = Mock()
        response.json.return_value = []
        mock_get.return_value = response
        start = datetime(
            2026,
            8,
            3,
            9,
            tzinfo=timezone(timedelta(hours=-4)),
        )
        end = datetime(
            2026,
            8,
            4,
            9,
            tzinfo=timezone(timedelta(hours=-4)),
        )

        EODHDProvider(api_key="test-token").get_1h("AAPL.US", start, end)

        params = mock_get.call_args.kwargs["params"]
        self.assertIsInstance(params["from"], int)
        self.assertIsInstance(params["to"], int)
        self.assertEqual(params["from"], int(start.timestamp()))
        self.assertEqual(params["to"], int(end.timestamp()))

    @patch("src.providers.requests.get")
    def test_get_1h_converts_date_bounds_to_utc_timestamps(
        self,
        mock_get: Mock,
    ) -> None:
        response = Mock()
        response.json.return_value = []
        mock_get.return_value = response

        EODHDProvider(api_key="test-token").get_1h(
            "AAPL.US",
            date(2026, 8, 3),
            date(2026, 8, 4),
        )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(
            params["from"],
            int(datetime.combine(date(2026, 8, 3), time.min, tzinfo=timezone.utc).timestamp()),
        )
        self.assertEqual(
            params["to"],
            int(datetime.combine(date(2026, 8, 4), time.min, tzinfo=timezone.utc).timestamp()),
        )

    @patch("src.providers.requests.get")
    def test_request_error_does_not_expose_authenticated_url(
        self,
        mock_get: Mock,
    ) -> None:
        token = "test-token"
        authenticated_url = (
            "https://eodhd.com/api/intraday/AAPL.US?api_token=test-token"
        )
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError(
            f"403 Client Error: Forbidden for url: {authenticated_url}"
        )
        mock_get.return_value = response

        with self.assertRaises(ProviderError) as error:
            EODHDProvider(api_key=token).get_1h(
                "AAPL.US",
                datetime(2026, 8, 3, tzinfo=timezone.utc),
                datetime(2026, 8, 4, tzinfo=timezone.utc),
            )

        self.assertNotIn(token, str(error.exception))
        self.assertNotIn("https://", str(error.exception))


if __name__ == "__main__":
    unittest.main()