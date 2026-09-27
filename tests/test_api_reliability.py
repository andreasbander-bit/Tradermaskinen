from copy import deepcopy
import unittest

import requests

from src.api_reliability import run_api_reliability
from src.providers import TwelveDataProvider


def bar(timestamp: str = "2026-08-03 13:30:00") -> dict[str, str]:
    return {
        "datetime": timestamp,
        "open": "100.0",
        "high": "102.0",
        "low": "99.0",
        "close": "101.0",
        "volume": "1000",
    }


class FakeResponse:
    def __init__(
        self,
        payload: dict,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict:
        return deepcopy(self.payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class ApiReliabilityTests(unittest.TestCase):
    def run_report(
        self,
        responses: list[FakeResponse | BaseException],
        *,
        symbols: tuple[str, ...] = ("AAPL",),
        burst_count: int = 0,
        clock=None,
    ):
        calls = 0

        def request_get(url: str, **kwargs):
            nonlocal calls
            calls += 1
            item = responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        report = run_api_reliability(
            TwelveDataProvider(api_key="mock-key"),
            normal_symbols=symbols,
            burst_symbol="VOLV.B",
            burst_request_count=burst_count,
            request_get=request_get,
            **({"clock": clock} if clock is not None else {}),
        )
        return report, calls

    @staticmethod
    def ok_response(headers: dict[str, str] | None = None) -> FakeResponse:
        return FakeResponse(
            {"status": "ok", "values": [bar()]},
            headers=headers,
        )

    def test_success_captures_latency_record_count_and_rate_metadata(self) -> None:
        ticks = iter((10.0, 10.125))
        report, calls = self.run_report(
            [self.ok_response({"X-RateLimit-Remaining": "42"})],
            clock=lambda: next(ticks),
        )

        metric = report.normal_requests[0]
        self.assertEqual(calls, 1)
        self.assertEqual(metric.classification, "SUCCESS")
        self.assertEqual(metric.http_status, 200)
        self.assertEqual(metric.record_count, 1)
        self.assertAlmostEqual(metric.elapsed_ms, 125.0)
        self.assertEqual(metric.rate_limit_metadata, (("x-ratelimit-remaining", "42"),))

    def test_http_429_is_rate_limited(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 429}, status_code=429)]
        )

        metric = report.normal_requests[0]
        self.assertEqual(metric.classification, "RATE_LIMITED")
        self.assertTrue(metric.rate_limited)
        self.assertTrue(metric.provider_limit)

    def test_http_200_with_rate_limit_api_code_is_rate_limited(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 429, "message": "Rate limit"})]
        )

        self.assertEqual(report.normal_requests[0].classification, "RATE_LIMITED")

    def test_http_200_rate_limit_message_without_code_is_rate_limited(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "message": "Rate limit exceeded"})]
        )

        self.assertEqual(report.normal_requests[0].classification, "RATE_LIMITED")

    def test_http_200_with_api_error_payload_is_api_error(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 401, "message": "Invalid request"})]
        )

        self.assertEqual(report.normal_requests[0].classification, "API_ERROR")
        self.assertEqual(report.normal_requests[0].http_status, 200)

    def test_http_404_is_unavailable(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 404}, status_code=404)]
        )

        self.assertEqual(report.normal_requests[0].classification, "UNAVAILABLE")

    def test_other_http_4xx_is_http_error(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 400}, status_code=400)]
        )

        self.assertEqual(report.normal_requests[0].classification, "HTTP_ERROR")

    def test_http_5xx_is_provider_limitation(self) -> None:
        report, _ = self.run_report(
            [FakeResponse({"status": "error", "code": 503}, status_code=503)]
        )

        self.assertEqual(report.normal_requests[0].classification, "PROVIDER_LIMITATION")

    def test_timeout_exception_is_timeout(self) -> None:
        report, calls = self.run_report([requests.Timeout()])

        self.assertEqual(calls, 1)
        self.assertEqual(report.normal_requests[0].classification, "TIMEOUT")
        self.assertTrue(report.normal_requests[0].timeout)

    def test_network_exception_is_network_error(self) -> None:
        report, _ = self.run_report([requests.ConnectionError()])

        self.assertEqual(report.normal_requests[0].classification, "NETWORK_ERROR")

    def test_rate_limit_metadata_absent_is_reported_unavailable(self) -> None:
        report, _ = self.run_report([self.ok_response()])

        self.assertEqual(report.burst_metadata_status, "UNAVAILABLE")
        self.assertEqual(report.burst_rate_limit_metadata, ())

    def test_burst_stops_after_first_rate_limit_without_retry(self) -> None:
        report, calls = self.run_report(
            [
                self.ok_response(),
                FakeResponse({"status": "error", "code": 429}, status_code=429),
                self.ok_response(),
            ],
            burst_count=5,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(len(report.burst_requests), 1)
        self.assertEqual(report.burst_rate_limit_sequence, 1)
        self.assertEqual(report.burst_rate_limit_http_status, 429)
        self.assertEqual(report.retry_policy_tested, "NO")

    def test_burst_only_uses_five_identical_aapl_requests(self) -> None:
        captured_params = []

        def request_get(url: str, **kwargs):
            captured_params.append(
                {key: value for key, value in kwargs["params"].items() if key != "apikey"}
            )
            return self.ok_response()

        report = run_api_reliability(
            TwelveDataProvider(api_key="mock-key"),
            normal_symbols=(),
            burst_request_count=5,
            request_get=request_get,
        )

        self.assertEqual(report.normal_requests, ())
        self.assertEqual(len(report.burst_requests), 5)
        self.assertTrue(all(metric.symbol == "AAPL" for metric in report.burst_requests))
        self.assertEqual(len(captured_params), 5)
        self.assertTrue(all(params == captured_params[0] for params in captured_params))
        self.assertEqual(captured_params[0]["interval"], "1h")
        self.assertEqual(captured_params[0]["start_date"], "2026-08-03 00:00:00")
        self.assertEqual(captured_params[0]["end_date"], "2026-08-04 00:00:00")

    def test_latency_summary_uses_only_measured_network_durations(self) -> None:
        ticks = iter((0.0, 0.1, 1.0, 1.2, 2.0, 2.3))
        report, calls = self.run_report(
            [self.ok_response(), self.ok_response(), self.ok_response()],
            symbols=("AAPL", "TSLA", "INVE.B"),
            clock=lambda: next(ticks),
        )

        self.assertEqual(calls, 3)
        self.assertEqual(report.normal_latency.sample_count, 3)
        self.assertAlmostEqual(report.normal_latency.minimum_ms, 100.0)
        self.assertAlmostEqual(report.normal_latency.median_ms, 200.0)
        self.assertAlmostEqual(report.normal_latency.mean_ms, 200.0)
        self.assertAlmostEqual(report.normal_latency.maximum_ms, 300.0)
        self.assertIsNone(report.normal_latency.p95_ms)

    def test_normal_rate_limit_skips_remaining_requests_and_burst(self) -> None:
        report, calls = self.run_report(
            [FakeResponse({"status": "error", "code": 429}, status_code=429)],
            symbols=("AAPL", "TSLA"),
            burst_count=5,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(report.normal_requests[1].classification, "NOT_EXECUTED")
        self.assertEqual(report.burst_requests[0].classification, "NOT_EXECUTED")
        self.assertIsNotNone(report.burst_skipped_reason)

    def test_burst_count_cannot_exceed_conservative_limit(self) -> None:
        with self.assertRaises(ValueError):
            self.run_report([self.ok_response()], burst_count=11)


if __name__ == "__main__":
    unittest.main()