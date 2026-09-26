from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import unittest

from src.historical_depth import (
    TwelveDataHistoricalProbe,
    print_historical_depth_report,
)
from src.providers import TwelveDataProvider


def row(timestamp: str, price: float) -> dict[str, str]:
    return {
        "datetime": timestamp,
        "open": str(price),
        "high": str(price + 1),
        "low": str(price - 1),
        "close": str(price),
        "volume": "100",
    }


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class HistoricalDepthTests(unittest.TestCase):
    def make_probe(self, claim: str, rows: list[dict], **options):
        def request_get(url: str, *, params: dict, timeout: int) -> FakeResponse:
            if url.endswith("/earliest_timestamp"):
                return FakeResponse({"datetime": claim})
            start = datetime.strptime(
                params["start_date"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            end = datetime.strptime(
                params["end_date"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            values = []
            for item in rows:
                timestamp = datetime.strptime(
                    item["datetime"], "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if start <= timestamp <= end:
                    values.append(item)
            return FakeResponse({"values": values})

        return TwelveDataHistoricalProbe(
            TwelveDataProvider(api_key="test-token"),
            request_get=request_get,
            **options,
        )

    def test_multi_year_dataset_reports_actual_earliest_and_latest(self) -> None:
        rows = [
            row("2020-01-02 14:30:00", 100),
            row("2021-01-04 14:30:00", 110),
            row("2022-01-03 14:30:00", 120),
            row("2023-01-03 14:30:00", 130),
        ]
        probe = self.make_probe(
            "2020-01-02 14:30:00",
            rows,
            chunk_days=365,
            claim_buffer_days=0,
        )

        report = probe.run(
            "AAPL",
            datetime(2023, 1, 3, 23, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(report.status, "AVAILABLE")
        self.assertEqual(report.provider_documentation_claim_raw, "2020-01-02 14:30:00")
        self.assertEqual(report.earliest_raw_timestamp, "2020-01-02 14:30:00")
        self.assertEqual(
            report.earliest_timestamp_utc,
            datetime(2020, 1, 2, 14, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(report.latest_raw_timestamp, "2023-01-03 14:30:00")
        self.assertEqual(
            report.latest_timestamp_utc,
            datetime(2023, 1, 3, 14, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(report.one_hour_rth_bars, 4)
        self.assertEqual(report.trading_days, 4)
        self.assertEqual(
            [(item.year, item.bars) for item in report.bars_per_calendar_year],
            [(2020, 1), (2021, 1), (2022, 1), (2023, 1)],
        )

    def test_inclusive_chunk_boundary_is_deduplicated(self) -> None:
        rows = [
            row("2026-08-03 13:30:00", 100),
            row("2026-08-05 13:30:00", 110),
        ]
        probe = self.make_probe(
            "2026-08-03 13:30:00",
            rows,
            chunk_days=2,
            claim_buffer_days=0,
        )

        report = probe.run(
            "AAPL",
            datetime(2026, 8, 7, 13, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(report.one_hour_rth_bars, 2)
        self.assertEqual(report.duplicate_bars_removed, 1)
        self.assertEqual(report.status, "AVAILABLE")

    def test_empty_internal_chunk_is_reported_as_partial(self) -> None:
        rows = [
            row("2020-01-02 14:30:00", 100),
            row("2022-01-03 14:30:00", 120),
        ]
        probe = self.make_probe(
            "2020-01-02 14:30:00",
            rows,
            chunk_days=365,
            claim_buffer_days=0,
        )

        report = probe.run(
            "AAPL",
            datetime(2023, 1, 3, 23, 59, tzinfo=timezone.utc),
        )

        self.assertEqual(report.status, "PARTIAL")
        self.assertTrue(report.empty_periods)

    def test_http_429_is_reported_as_provider_limitation(self) -> None:
        def request_get(url: str, *, params: dict, timeout: int) -> FakeResponse:
            if url.endswith("/earliest_timestamp"):
                return FakeResponse({"datetime": "2020-01-02 14:30:00"})
            return FakeResponse({"code": 429, "status": "error"}, status_code=429)

        probe = TwelveDataHistoricalProbe(
            TwelveDataProvider(api_key="test-token"),
            request_get=request_get,
            chunk_days=365,
            claim_buffer_days=0,
        )

        report = probe.run(
            "AAPL",
            datetime(2021, 1, 2, 14, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(report.status, "PROVIDER_LIMITATION")
        self.assertEqual(report.one_hour_rth_bars, 0)
        self.assertTrue(any(issue.code == "429" for issue in report.request_issues))
        output = StringIO()
        with redirect_stdout(output):
            print_historical_depth_report(report)
        self.assertIn(
            "Latest available history: UNDETERMINED",
            output.getvalue(),
        )

        def error_payload_request(
            url: str,
            *,
            params: dict,
            timeout: int,
        ) -> FakeResponse:
            if url.endswith("/earliest_timestamp"):
                return FakeResponse({"datetime": "2020-01-02 14:30:00"})
            return FakeResponse(
                {
                    "status": "error",
                    "code": "test-token",
                    "message": "rate limit reached: test-token",
                }
            )

        safe_probe = TwelveDataHistoricalProbe(
            TwelveDataProvider(api_key="test-token"),
            request_get=error_payload_request,
            chunk_days=365,
            claim_buffer_days=0,
        )
        safe_report = safe_probe.run(
            "AAPL",
            datetime(2021, 1, 2, 14, 30, tzinfo=timezone.utc),
        )
        safe_output = StringIO()
        with redirect_stdout(safe_output):
            print_historical_depth_report(safe_report)
        self.assertNotIn("test-token", safe_output.getvalue())


if __name__ == "__main__":
    unittest.main()