from copy import deepcopy
import unittest

from src.providers import TwelveDataProvider
from src.repeat_download import RepeatInstrument, run_repeat_downloads


def bar(timestamp: str, *, open_value: str = "100.0", volume: str = "1000") -> dict[str, str]:
    return {
        "datetime": timestamp,
        "open": open_value,
        "high": "102.0",
        "low": "99.0",
        "close": "101.0",
        "volume": volume,
    }


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return deepcopy(self.payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


class RepeatDownloadTests(unittest.TestCase):
    instrument = RepeatInstrument("AAPL", "AAPL", "United States", "US")

    def run_repeats(self, responses: list[FakeResponse]):
        calls: list[tuple[str, dict]] = []

        def request_get(url: str, *, params: dict, timeout: int) -> FakeResponse:
            calls.append((url, {key: value for key, value in params.items() if key != "apikey"}))
            return responses.pop(0)

        report = run_repeat_downloads(
            TwelveDataProvider(api_key="mock-key"),
            (self.instrument,),
            request_get=request_get,
        )
        return report.results[0], calls

    def response(self, values: list[dict], status_code: int = 200) -> FakeResponse:
        return FakeResponse({"status": "ok", "meta": {"symbol": "AAPL"}, "values": values}, status_code)

    def test_identical_repeated_response_passes(self) -> None:
        values = [bar("2026-08-03 13:30:00"), bar("2026-08-03 14:30:00", open_value="101.0")]

        result, calls = self.run_repeats([self.response(values), self.response(values)])

        self.assertEqual(result.status, "PASS")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(result.first.raw_payload["values"][0]["open"], "100.0")

    def test_changed_ohlc_value_is_detected(self) -> None:
        first = [bar("2026-08-03 13:30:00")]
        second = [bar("2026-08-03 13:30:00", open_value="100.25")]

        result, _ = self.run_repeats([self.response(first), self.response(second)])

        self.assertEqual(result.status, "DIFFERENCE_DETECTED")
        self.assertEqual(result.differences[0].field, "open")
        self.assertEqual(result.differences[0].first_value, 100.0)
        self.assertEqual(result.differences[0].second_value, 100.25)

    def test_changed_volume_is_detected(self) -> None:
        first = [bar("2026-08-03 13:30:00", volume="1000")]
        second = [bar("2026-08-03 13:30:00", volume="1001")]

        result, _ = self.run_repeats([self.response(first), self.response(second)])

        self.assertEqual(result.status, "DIFFERENCE_DETECTED")
        self.assertEqual(result.differences[0].field, "volume")

    def test_missing_timestamp_is_detected(self) -> None:
        first = [bar("2026-08-03 13:30:00"), bar("2026-08-03 14:30:00")]
        second = [bar("2026-08-03 13:30:00")]

        result, _ = self.run_repeats([self.response(first), self.response(second)])

        self.assertEqual(result.status, "DIFFERENCE_DETECTED")
        self.assertEqual(result.missing_from_second, ("2026-08-03T14:30:00+00:00",))

    def test_additional_timestamp_is_detected(self) -> None:
        first = [bar("2026-08-03 13:30:00")]
        second = [bar("2026-08-03 13:30:00"), bar("2026-08-03 14:30:00")]

        result, _ = self.run_repeats([self.response(first), self.response(second)])

        self.assertEqual(result.status, "DIFFERENCE_DETECTED")
        self.assertEqual(result.missing_from_first, ("2026-08-03T14:30:00+00:00",))

    def test_reordered_json_records_and_keys_do_not_change_market_data_status(self) -> None:
        first_rows = [bar("2026-08-03 13:30:00"), bar("2026-08-03 14:30:00", open_value="101.0")]
        second_rows = [dict(reversed(tuple(row.items()))) for row in reversed(first_rows)]

        result, _ = self.run_repeats([self.response(first_rows), self.response(second_rows)])

        self.assertEqual(result.status, "PASS")
        self.assertTrue(result.raw_order_changed)
        self.assertTrue(result.raw_field_order_changed)

    def test_second_request_provider_failure_is_provider_limitation(self) -> None:
        values = [bar("2026-08-03 13:30:00")]

        result, _ = self.run_repeats([self.response(values), self.response([], 429)])

        self.assertEqual(result.first.request_status, "AVAILABLE")
        self.assertEqual(result.second.request_status, "PROVIDER_LIMITATION")
        self.assertEqual(result.status, "PROVIDER_LIMITATION")

        api_limited = FakeResponse(
            {"status": "error", "code": 429, "message": "rate limit"}
        )
        result, _ = self.run_repeats([self.response(values), api_limited])
        self.assertEqual(result.second.request_status, "PROVIDER_LIMITATION")
        self.assertEqual(result.status, "PROVIDER_LIMITATION")

    def test_http_404_is_unavailable_and_comparison_not_executed(self) -> None:
        values = [bar("2026-08-03 13:30:00")]

        result, _ = self.run_repeats([self.response(values), self.response([], 404)])

        self.assertEqual(result.second.request_status, "UNAVAILABLE")
        self.assertEqual(result.status, "NOT_EXECUTED")

    def test_duplicate_timestamp_introduced_is_detected(self) -> None:
        values = [bar("2026-08-03 13:30:00")]

        result, _ = self.run_repeats([self.response(values), self.response(values + values)])

        self.assertEqual(result.status, "DIFFERENCE_DETECTED")
        self.assertEqual(result.duplicates_second, ("2026-08-03T13:30:00+00:00",))

    def test_raw_provider_values_are_preserved_and_format_only_change_passes(self) -> None:
        first = [bar("2026-08-03 13:30:00", open_value="100.0")]
        second = [bar("2026-08-03 13:30:00", open_value="100.00")]

        result, _ = self.run_repeats([self.response(first), self.response(second)])

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.first.raw_payload["values"][0]["open"], "100.0")
        self.assertEqual(result.second.raw_payload["values"][0]["open"], "100.00")
        self.assertTrue(result.raw_representation_changes[0].numeric_meaning_same)


if __name__ == "__main__":
    unittest.main()