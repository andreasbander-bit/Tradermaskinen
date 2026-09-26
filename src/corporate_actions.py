from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

import requests

from src.providers import MarketBar, TwelveDataProvider
from src.sessions import NEW_YORK, classify_us_timestamp


CORPORATE_ACTION_DOC_CLAIM = (
    "Twelve Data documents separate /splits and /dividends endpoints, each "
    "at 20 credits per symbol and Grow/Venture plan access; time_series "
    "documents adjust as a boolean with a default of true."
)
SYMBOL_CONTINUITY_DOC_CLAIM = (
    "Twelve Data documents /symbol_search metadata and /cross_listings; "
    "these are not a symbol-change history service."
)
ADJUSTMENT_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class EndpointResult:
    endpoint: str
    status: str
    raw_metadata: dict[str, Any]
    raw_records: tuple[dict[str, Any], ...]
    error_code: str | None


@dataclass(frozen=True)
class RawHourlyBar:
    raw_timestamp: str
    timestamp_utc: datetime
    raw_values: dict[str, Any]
    normalized_bar: MarketBar


@dataclass(frozen=True)
class SeriesResult:
    status: str
    adjust: str
    start_utc: datetime
    end_utc: datetime
    raw_bars: tuple[RawHourlyBar, ...]
    rth_bars: tuple[MarketBar, ...]
    non_rth_bars_excluded: int
    error_code: str | None


@dataclass(frozen=True)
class FieldAdjustmentDifference:
    timestamp: str
    field: str
    adjust_true_value: float
    adjust_false_value: float
    absolute_difference: float


@dataclass(frozen=True)
class AdjustmentComparison:
    status: str
    matching_timestamps: int
    missing_from_adjust_true: tuple[str, ...]
    missing_from_adjust_false: tuple[str, ...]
    differences: tuple[FieldAdjustmentDifference, ...]
    max_absolute_difference_by_field: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class SessionAroundEvent:
    previous_session_date: str | None
    previous_session_close: float | None
    previous_session_volume: float | None
    event_session_date: str | None
    event_session_open: float | None
    event_session_close: float | None
    event_session_volume: float | None


@dataclass(frozen=True)
class EventWindowResult:
    event_type: str
    raw_event: dict[str, Any]
    start_utc: datetime
    end_utc: datetime
    adjust_true: SeriesResult
    adjust_false: SeriesResult
    comparison: AdjustmentComparison
    session_observation: SessionAroundEvent


@dataclass(frozen=True)
class CorporateActionReport:
    symbol: str
    split_endpoint: EndpointResult
    dividend_endpoint: EndpointResult
    symbol_search: EndpointResult
    split_window: EventWindowResult | None
    dividend_window: EventWindowResult | None
    split_comparison_status: str
    dividend_comparison_status: str


def _safe_code(value: Any) -> str | None:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdigit() and len(value) <= 6:
        return value
    return None


def _endpoint_status(status_code: int | None, code: str | None, message: str) -> str:
    if status_code in (402, 403, 429) or code in {"402", "403", "429"}:
        return "PROVIDER_LIMITATION"
    if any(
        indicator in message.lower()
        for indicator in ("entitlement", "subscription", "credit limit", "rate limit")
    ):
        return "PROVIDER_LIMITATION"
    return "UNAVAILABLE"


class TwelveDataCorporateActionsProbe:
    def __init__(
        self,
        provider: TwelveDataProvider,
        *,
        request_get: Callable[..., Any] | None = None,
        window_days: int = 7,
    ) -> None:
        if window_days <= 0:
            raise ValueError("window_days must be positive.")
        self.provider = provider
        self.request_get = request_get or requests.get
        self.window_days = window_days

    def _request(self, endpoint: str, params: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            response = self.request_get(
                f"{self.provider.BASE_URL}/{endpoint}",
                params=params,
                timeout=30,
            )
        except requests.RequestException as error:
            status_code = (
                error.response.status_code if error.response is not None else None
            )
            return _endpoint_status(status_code, None, ""), None, (
                str(status_code) if status_code is not None else None
            )
        except Exception:
            return "UNAVAILABLE", None, None

        status_code = getattr(response, "status_code", None)
        if status_code is not None and status_code >= 400:
            return (
                _endpoint_status(status_code, None, ""),
                None,
                str(status_code),
            )
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return "PARTIAL", None, None
        if not isinstance(payload, dict):
            return "PARTIAL", None, None
        if payload.get("status") == "error":
            code = _safe_code(payload.get("code"))
            message = str(payload.get("message", ""))
            return _endpoint_status(status_code, code, message), None, code
        return "AVAILABLE", payload, None

    def _fetch_actions(
        self,
        endpoint: str,
        response_key: str,
        symbol: str,
    ) -> EndpointResult:
        status, payload, error_code = self._request(
            endpoint,
            {"symbol": symbol, "apikey": self.provider.api_key},
        )
        if payload is None:
            return EndpointResult(endpoint, status, {}, (), error_code)

        records = payload.get(response_key)
        metadata = payload.get("meta", {})
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(records, list):
            return EndpointResult("/" + endpoint, "PARTIAL", metadata, (), None)
        raw_records = tuple(dict(record) for record in records if isinstance(record, dict))
        record_count_differs = len(raw_records) != len(records)
        return EndpointResult(
            endpoint="/" + endpoint,
            status="PARTIAL" if record_count_differs else "AVAILABLE",
            raw_metadata=dict(metadata),
            raw_records=raw_records,
            error_code=None,
        )

    def _fetch_symbol_search(self, symbol: str) -> EndpointResult:
        status, payload, error_code = self._request(
            "symbol_search",
            {"symbol": symbol, "apikey": self.provider.api_key},
        )
        if payload is None:
            return EndpointResult("/symbol_search", status, {}, (), error_code)
        matches = payload.get("data")
        if not isinstance(matches, list):
            return EndpointResult("/symbol_search", "PARTIAL", {}, (), None)
        raw_records = tuple(
            dict(item)
            for item in matches
            if isinstance(item, dict) and item.get("symbol") == symbol
        )
        return EndpointResult(
            "/symbol_search",
            "AVAILABLE" if raw_records else "PARTIAL",
            {},
            raw_records,
            None,
        )

    def _fetch_series(
        self,
        symbol: str,
        start_utc: datetime,
        end_utc: datetime,
        adjust: str,
    ) -> SeriesResult:
        params = {
            "symbol": symbol,
            "interval": "1h",
            "start_date": start_utc.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "end_date": end_utc.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "timezone": "UTC",
            "outputsize": 5000,
            "adjust": adjust,
            "apikey": self.provider.api_key,
        }
        status, payload, error_code = self._request("time_series", params)
        if payload is None:
            return SeriesResult(
                status,
                adjust,
                start_utc,
                end_utc,
                (),
                (),
                0,
                error_code,
            )

        values = payload.get("values")
        if not isinstance(values, list):
            return SeriesResult(
                "PARTIAL",
                adjust,
                start_utc,
                end_utc,
                (),
                (),
                0,
                None,
            )

        raw_bars: list[RawHourlyBar] = []
        rth_bars: list[MarketBar] = []
        excluded = 0
        malformed = False
        for value in values:
            if not isinstance(value, dict):
                malformed = True
                continue
            try:
                raw_timestamp = value["datetime"]
                if not isinstance(raw_timestamp, str):
                    raise ValueError("timestamp must be text")
                parsed = datetime.fromisoformat(
                    raw_timestamp.replace("Z", "+00:00")
                )
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                timestamp_utc = parsed.astimezone(timezone.utc)
                bar = MarketBar(
                    symbol=symbol,
                    timestamp=timestamp_utc,
                    open=float(value["open"]),
                    high=float(value["high"]),
                    low=float(value["low"]),
                    close=float(value["close"]),
                    volume=float(value["volume"]),
                    provider="Twelve Data",
                )
            except (KeyError, TypeError, ValueError):
                malformed = True
                continue

            raw_bars.append(
                RawHourlyBar(raw_timestamp, timestamp_utc, dict(value), bar)
            )
            if classify_us_timestamp(timestamp_utc).is_rth:
                rth_bars.append(bar)
            else:
                excluded += 1

        if not rth_bars:
            series_status = "UNAVAILABLE" if not malformed else "PARTIAL"
        else:
            series_status = "PARTIAL" if malformed else "AVAILABLE"
        return SeriesResult(
            series_status,
            adjust,
            start_utc,
            end_utc,
            tuple(raw_bars),
            tuple(sorted(rth_bars, key=lambda bar: bar.timestamp)),
            excluded,
            None,
        )

    @staticmethod
    def _compare_series(
        adjusted: SeriesResult,
        unadjusted: SeriesResult,
    ) -> AdjustmentComparison:
        if adjusted.status != "AVAILABLE" or unadjusted.status != "AVAILABLE":
            return AdjustmentComparison(
                "NOT_EXECUTED",
                0,
                (),
                (),
                (),
                (),
            )
        adjusted_by_time = {bar.timestamp: bar for bar in adjusted.rth_bars}
        unadjusted_by_time = {bar.timestamp: bar for bar in unadjusted.rth_bars}
        adjusted_timestamps = set(adjusted_by_time)
        unadjusted_timestamps = set(unadjusted_by_time)
        matching = sorted(adjusted_timestamps & unadjusted_timestamps)
        missing_adjusted = tuple(
            timestamp.isoformat()
            for timestamp in sorted(unadjusted_timestamps - adjusted_timestamps)
        )
        missing_unadjusted = tuple(
            timestamp.isoformat()
            for timestamp in sorted(adjusted_timestamps - unadjusted_timestamps)
        )
        differences: list[FieldAdjustmentDifference] = []
        maxima: dict[str, float] = {}
        for timestamp in matching:
            adjusted_bar = adjusted_by_time[timestamp]
            unadjusted_bar = unadjusted_by_time[timestamp]
            for field in ADJUSTMENT_FIELDS:
                adjusted_value = getattr(adjusted_bar, field)
                unadjusted_value = getattr(unadjusted_bar, field)
                absolute_difference = abs(adjusted_value - unadjusted_value)
                maxima[field] = max(maxima.get(field, 0.0), absolute_difference)
                if absolute_difference:
                    differences.append(
                        FieldAdjustmentDifference(
                            timestamp.isoformat(),
                            field,
                            adjusted_value,
                            unadjusted_value,
                            absolute_difference,
                        )
                    )
        if not matching:
            status = "NOT_EXECUTED"
        elif differences or missing_adjusted or missing_unadjusted:
            status = "DIFFERENCE_DETECTED"
        else:
            status = "AVAILABLE"
        return AdjustmentComparison(
            status,
            len(matching),
            missing_adjusted,
            missing_unadjusted,
            tuple(differences),
            tuple(sorted(maxima.items())),
        )

    @staticmethod
    def _session_observation(
        series: SeriesResult,
        event_date: date,
    ) -> SessionAroundEvent:
        by_date: dict[date, list[MarketBar]] = {}
        for bar in series.rth_bars:
            session = classify_us_timestamp(bar.timestamp)
            by_date.setdefault(date.fromisoformat(session.session_date), []).append(bar)
        before_dates = [session_date for session_date in by_date if session_date < event_date]
        if not before_dates or event_date not in by_date:
            return SessionAroundEvent(None, None, None, None, None, None, None)
        previous_date = max(before_dates)
        previous_bars = sorted(by_date[previous_date], key=lambda bar: bar.timestamp)
        event_bars = sorted(by_date[event_date], key=lambda bar: bar.timestamp)
        return SessionAroundEvent(
            previous_session_date=previous_date.isoformat(),
            previous_session_close=previous_bars[-1].close,
            previous_session_volume=sum(bar.volume for bar in previous_bars),
            event_session_date=event_date.isoformat(),
            event_session_open=event_bars[0].open,
            event_session_close=event_bars[-1].close,
            event_session_volume=sum(bar.volume for bar in event_bars),
        )

    def _event_window(
        self,
        symbol: str,
        event_type: str,
        event: dict[str, Any],
        event_date: date,
    ) -> EventWindowResult:
        start_utc = datetime.combine(
            event_date - timedelta(days=self.window_days),
            time.min,
            tzinfo=NEW_YORK,
        ).astimezone(timezone.utc)
        end_utc = datetime.combine(
            event_date + timedelta(days=self.window_days + 1),
            time.min,
            tzinfo=NEW_YORK,
        ).astimezone(timezone.utc)
        adjusted = self._fetch_series(symbol, start_utc, end_utc, "true")
        unadjusted = self._fetch_series(symbol, start_utc, end_utc, "false")
        comparison = self._compare_series(adjusted, unadjusted)
        observation = self._session_observation(adjusted, event_date)
        return EventWindowResult(
            event_type,
            dict(event),
            start_utc,
            end_utc,
            adjusted,
            unadjusted,
            comparison,
            observation,
        )

    @staticmethod
    def _valid_event_date(event: dict[str, Any], field: str) -> date | None:
        raw_value = event.get(field)
        if not isinstance(raw_value, str):
            return None
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return None

    def run(self, symbol: str) -> CorporateActionReport:
        splits = self._fetch_actions("splits", "splits", symbol)
        dividends = self._fetch_actions("dividends", "dividends", symbol)
        symbol_search = self._fetch_symbol_search(symbol)

        valid_splits = [
            (event_date, event)
            for event in splits.raw_records
            if (event_date := self._valid_event_date(event, "date")) is not None
        ]
        valid_dividends = [
            (event_date, event)
            for event in dividends.raw_records
            if (event_date := self._valid_event_date(event, "ex_date")) is not None
        ]

        split_window = None
        if splits.status == "AVAILABLE" and valid_splits:
            event_date, event = max(valid_splits, key=lambda item: item[0])
            split_window = self._event_window(
                symbol,
                "split",
                event,
                event_date,
            )

        dividend_window = None
        if dividends.status == "AVAILABLE" and valid_dividends:
            event_date, event = max(valid_dividends, key=lambda item: item[0])
            dividend_window = self._event_window(
                symbol,
                "dividend",
                event,
                event_date,
            )

        split_status = (
            split_window.comparison.status
            if split_window is not None
            else "NOT_EXECUTED"
        )
        dividend_status = (
            dividend_window.comparison.status
            if dividend_window is not None
            else "NOT_EXECUTED"
        )
        return CorporateActionReport(
            symbol,
            splits,
            dividends,
            symbol_search,
            split_window,
            dividend_window,
            split_status,
            dividend_status,
        )


def _print_endpoint(result: EndpointResult) -> None:
    print(f"{result.endpoint} status: {result.status}")
    print(f"{result.endpoint} events/records returned: {len(result.raw_records)}")
    if result.error_code:
        print(f"{result.endpoint} API/HTTP code: {result.error_code}")
    for record in result.raw_records:
        fields = (
            "date",
            "ex_date",
            "amount",
            "ratio",
            "numerator",
            "denominator",
            "symbol",
            "instrument_name",
            "exchange",
            "mic_code",
            "instrument_type",
            "figi",
            "isin",
            "cusip",
        )
        print("  returned fields:", {field: record[field] for field in fields if field in record})


def _print_series(label: str, series: SeriesResult) -> None:
    print(f"{label} series status: {series.status}")
    print(f"{label} raw rows: {len(series.raw_bars)}")
    print(f"{label} RTH bars: {len(series.rth_bars)}")
    print(f"{label} non-RTH bars excluded: {series.non_rth_bars_excluded}")


def _print_event_window(window: EventWindowResult | None, action_type: str) -> None:
    print(f"\n{action_type} adjusted/unadjusted comparison:")
    if window is None:
        print("Status: NOT_EXECUTED (no returned event with a usable date)")
        return
    print(f"Event date: {window.raw_event.get('date', window.raw_event.get('ex_date'))}")
    safe_fields = (
        "date",
        "ex_date",
        "amount",
        "ratio",
        "numerator",
        "denominator",
    )
    print(
        "Event values returned:",
        {field: window.raw_event[field] for field in safe_fields if field in window.raw_event},
    )
    print(f"Controlled 1H window UTC: {window.start_utc.isoformat()} -> {window.end_utc.isoformat()}")
    _print_series("adjust=true", window.adjust_true)
    _print_series("adjust=false", window.adjust_false)
    print(f"Adjusted vs unadjusted status: {window.comparison.status}")
    print(f"Matching UTC timestamps: {window.comparison.matching_timestamps}")
    print(
        "Timestamps missing from adjust=true:",
        len(window.comparison.missing_from_adjust_true),
    )
    print(
        "Timestamps missing from adjust=false:",
        len(window.comparison.missing_from_adjust_false),
    )
    print(f"OHLCV field differences: {len(window.comparison.differences)}")
    for difference in window.comparison.differences:
        print(
            f"  {difference.timestamp} {difference.field}: "
            f"adjust=true={difference.adjust_true_value} "
            f"adjust=false={difference.adjust_false_value} "
            f"absolute difference={difference.absolute_difference}"
        )
    if window.comparison.max_absolute_difference_by_field:
        print("Maximum absolute difference by field:")
        for field, maximum in window.comparison.max_absolute_difference_by_field:
            print(f"  {field}: {maximum}")
    else:
        print("No differing OHLCV fields on matching timestamps.")
    observation = window.session_observation
    print("Session boundary observation (adjust=true):")
    if observation.event_session_date is None:
        print("  Previous and event RTH sessions were not both available.")
    else:
        print(
            f"  previous {observation.previous_session_date} close="
            f"{observation.previous_session_close}; event "
            f"{observation.event_session_date} open="
            f"{observation.event_session_open} close="
            f"{observation.event_session_close}; previous volume="
            f"{observation.previous_session_volume}; event volume="
            f"{observation.event_session_volume}"
        )
        if (
            observation.previous_session_close is not None
            and observation.event_session_open is not None
        ):
            print(
                "  observed event open minus previous close="
                f"{observation.event_session_open - observation.previous_session_close:.6f}"
            )


def print_corporate_action_report(report: CorporateActionReport) -> None:
    print("\n=== DQ-10 CORPORATE ACTIONS ===")
    print(f"Instrument: {report.symbol}")
    print("Canonical series: 1H RTH")
    print("Provider documentation:", CORPORATE_ACTION_DOC_CLAIM)
    print("Symbol continuity documentation:", SYMBOL_CONTINUITY_DOC_CLAIM)
    print("\nProvider-returned split events:")
    _print_endpoint(report.split_endpoint)
    print("\nProvider-returned dividend events:")
    _print_endpoint(report.dividend_endpoint)
    print("\nSymbol/instrument metadata:")
    _print_endpoint(report.symbol_search)
    print("No symbol-change history is inferred from symbol-search/cross-listing metadata.")

    _print_event_window(report.split_window, "Split")
    if report.split_window is not None:
        event_ratio = report.split_window.raw_event.get("ratio")
        observation = report.split_window.session_observation
        print("Split price/volume observation:")
        print(f"  provider-returned ratio={event_ratio}")
        if observation.previous_session_close and observation.event_session_open:
            observed_ratio = (
                observation.event_session_open
                / observation.previous_session_close
            )
            print(
                "  observed event-open / prior-close ratio="
                f"{observed_ratio:.8f}"
            )
            try:
                split_factor = float(event_ratio)
            except (TypeError, ValueError):
                split_factor = None
            if split_factor and 0.85 <= observed_ratio <= 1.15:
                print(
                    "  observation: no split-factor-sized price discontinuity; "
                    "pre-event returned prices appear on a split-adjusted scale."
                )
            elif split_factor and (
                abs(observed_ratio - split_factor) <= split_factor * 0.15
                or abs(observed_ratio - 1.0 / split_factor)
                <= (1.0 / split_factor) * 0.15
            ):
                print(
                    "  observation: event-open/prior-close is near the provider's "
                    "reported split factor or reciprocal."
                )
            else:
                print(
                    "  observation: price scale is not conclusive relative to "
                    "the provider-returned split factor."
                )
        else:
            print("  adjacent-session price ratio unavailable")
        if (
            observation.previous_session_volume is not None
            and observation.event_session_volume is not None
            and observation.previous_session_volume != 0
        ):
            observed_volume_ratio = (
                observation.event_session_volume
                / observation.previous_session_volume
            )
            print(
                "  observed event-session / prior-session volume ratio="
                f"{observed_volume_ratio:.8f}"
            )
            if not any(
                difference.field == "volume"
                for difference in report.split_window.comparison.differences
            ):
                print(
                    "  adjust=true and adjust=false returned identical per-bar "
                    "volume in this window; no split-related volume adjustment "
                    "is demonstrated by the adjustment flag."
                )
        elif observation.previous_session_volume is None:
            print("  adjacent-session volume ratio unavailable")

    _print_event_window(report.dividend_window, "Dividend")
    if report.dividend_window is not None:
        comparison_status = report.dividend_window.comparison.status
        if comparison_status == "AVAILABLE":
            print(
                "Dividend observation: the event amount is provided separately; "
                "no adjust=true/false OHLCV difference was returned in this "
                "window. These values do not isolate whether the dividend is "
                "otherwise reflected in historical prices."
            )
        elif comparison_status == "DIFFERENCE_DETECTED":
            print(
                "Dividend observation: the event is available separately and "
                "the provider returned adjust=true/false OHLCV differences above."
            )
        else:
            print(
                "Dividend observation: the event is available separately, but "
                "adjusted-price reflection could not be compared."
            )