from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

import requests

from src.run_daily_validation import main


class DailyValidationRunnerTests(unittest.TestCase):
    @patch("src.run_daily_validation.TwelveDataProvider")
    def test_hourly_http_error_does_not_print_authenticated_url(
        self,
        provider_class: patch,
    ) -> None:
        token = "test-token"
        authenticated_url = (
            "https://api.twelvedata.com/time_series?apikey=test-token"
        )
        provider_class.return_value.get_1h.side_effect = requests.HTTPError(
            f"403 Client Error for url: {authenticated_url}"
        )
        output = StringIO()

        with redirect_stdout(output):
            main()

        rendered = output.getvalue()
        self.assertIn("1H source unavailable (HTTPError)", rendered)
        self.assertNotIn(token, rendered)
        self.assertNotIn("https://", rendered)


if __name__ == "__main__":
    unittest.main()