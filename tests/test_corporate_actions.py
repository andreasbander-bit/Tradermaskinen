from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from src.corporate_actions import TwelveDataCorporateActionsProbe
from src.providers import TwelveDataProvider


def bar_row(timestamp: str, *, open_price: float, volume: float) -> dict[str, str]:
    return {
        "datetime": timestamp,
        "open": str(open_price),
        "high": str(open_price + 2),
        "low": str(open_price - 2),
        "close": str(open_price + 1),
        "volume": str(volume),
    }


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload


class CorporateActionsTests(unittest.TestCase):
    def make_probe(
        self,
        *,
        splits: list[dict] | None = None,
        dividends: list[dict] | None = None,
        adjusted: list[dict] | None = None,
        unadjusted: list[dict] | None = None,
        split_status: int = 200,
    ) -> tuple[TwelveDataCorporateActionsProbe, Mock]:
        request_get = Mock()

        def side_effect(url: str, *, params: dict, timeout: int) -> FakeResponse:
            if url.endswith("/splits"):
                return FakeResponse(
                    {"meta": {"symbol": "AAPL"}, "splits": splits or []},
                    status_code=split_status,
                )
            if url.endswith("/dividends"):
                return FakeResponse(
                    {"meta": {"symbol": "AAPL"}, "dividends": dividends or []}
                )
            if url.endswith("/symbol_search"):
                return FakeResponse(
                    {
                        "data": [
                            {
                                "symbol": "AAPL",
                                "instrument_name": "Apple Inc",
                                "exchange": "NASDAQ",
                                "mic_code": "XNGS",
                                "instrument_type": "Common Stock",
                            }
                        ]
                    }
                )
            return FakeResponse(
                {"values": adjusted if params["adjust"] == "true" else unadjusted or []}
            )

        request_get.side_effect = side_effect
        return (
            TwelveDataCorporateActionsProbe(
                TwelveDataProvider(api_key="test-token"),
                request_get=request_get,
            ),
            request_get,
        )

    def test_known_split_event_is_preserved_and_window_comes_from_event(self) -> None:
        split = {"date": "2020-08-31", "ratio": 0.25}
        adjusted = [
            bar_row("2020-08-28 13:30:00", open_price=124, volume=400),
            bar_row("2020-08-31 13:30:00", open_price=127, volume=100),
        ]
        unadjusted = [
            bar_row("2020-08-28 13:30:00", open_price=496, volume=100),
            bar_row("2020-08-31 13:30:00", open_price=127, volume=100),
        ]
        probe, request_get = self.make_probe(
            splits=[split],
            adjusted=adjusted,
            unadjusted=unadjusted,
        )

        report = probe.run("AAPL")

        self.assertEqual(report.split_endpoint.status, "AVAILABLE")
        self.assertEqual(report.split_endpoint.raw_records, (split,))
        self.assertEqual(report.split_window.raw_event, split)
        self.assertEqual(
            report.split_window.session_observation.event_session_date,
            "2020-08-31",
        )
        self.assertEqual(report.split_comparison_status, "DIFFERENCE_DETECTED")
        time_series_calls = [
            call for call in request_get.call_args_list
            if call.args[0].endswith("/time_series")
        ]
        self.assertEqual(len(time_series_calls), 2)
        self.assertEqual(
            {call.kwargs["params"]["adjust"] for call in time_series_calls},
            {"true", "false"},
        )

    def test_known_dividend_event_is_reported_separately(self) -> None:
        dividend = {"ex_date": "2026-08-10", "amount": 0.27}
        rows = [
            bar_row("2026-08-07 13:30:00", open_price=228, volume=1000),
            bar_row("2026-08-10 13:30:00", open_price=227, volume=1100),
        ]
        probe, _ = self.make_probe(dividends=[dividend], adjusted=rows, unadjusted=rows)

        report = probe.run("AAPL")

        self.assertEqual(report.dividend_endpoint.status, "AVAILABLE")
        self.assertEqual(report.dividend_endpoint.raw_records, (dividend,))
        self.assertEqual(report.dividend_window.raw_event["amount"], 0.27)
        self.assertEqual(report.dividend_comparison_status, "AVAILABLE")

    def test_adjusted_and_unadjusted_ohlcv_are_compared_without_mutation(
        self,
    ) -> None:
        adjusted = [bar_row("2020-08-28 13:30:00", open_price=125, volume=400)]
        unadjusted = [bar_row("2020-08-28 13:30:00", open_price=500, volume=100)]
        probe, _ = self.make_probe(
            splits=[{"date": "2020-08-31", "ratio": 0.25}],
            adjusted=adjusted,
            unadjusted=unadjusted,
        )

        report = probe.run("AAPL")

        comparison = report.split_window.comparison
        self.assertEqual(comparison.status, "DIFFERENCE_DETECTED")
        self.assertIn("open", {item.field for item in comparison.differences})
        self.assertIn("volume", {item.field for item in comparison.differences})
        self.assertEqual(report.split_window.adjust_true.raw_bars[0].raw_values["open"], "125")
        self.assertEqual(report.split_window.adjust_false.raw_bars[0].raw_values["open"], "500")

    def test_successful_empty_action_responses_do_not_invent_events(self) -> None:
        probe, request_get = self.make_probe(splits=[], dividends=[])

        report = probe.run("AAPL")

        self.assertEqual(report.split_endpoint.status, "AVAILABLE")
        self.assertEqual(report.dividend_endpoint.status, "AVAILABLE")
        self.assertEqual(report.split_endpoint.raw_records, ())
        self.assertEqual(report.dividend_endpoint.raw_records, ())
        self.assertIsNone(report.split_window)
        self.assertIsNone(report.dividend_window)
        self.assertEqual(report.split_comparison_status, "NOT_EXECUTED")
        self.assertEqual(report.dividend_comparison_status, "NOT_EXECUTED")
        self.assertEqual(
            [call.args[0].rsplit("/", 1)[-1] for call in request_get.call_args_list],
            ["splits", "dividends", "symbol_search"],
        )

    def test_provider_limitation_is_reported_without_raw_error(self) -> None:
        probe, _ = self.make_probe(split_status=403)

        report = probe.run("AAPL")

        self.assertEqual(report.split_endpoint.status, "PROVIDER_LIMITATION")
        self.assertEqual(report.split_endpoint.error_code, "403")
        self.assertIsNone(report.split_window)
        self.assertEqual(report.split_comparison_status, "NOT_EXECUTED")


if __name__ == "__main__":
    unittest.main()