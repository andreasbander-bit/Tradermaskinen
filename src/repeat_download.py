from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Callable
from unittest.mock import patch

import requests

from src.providers import MarketBar, ProviderError, TwelveDataProvider
from src.sessions import classify_us_timestamp


FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class RepeatInstrument:
    requested_instrument: str
    provider_symbol: str
    country: str
    session_classifier: str | None
    identity_note: str | None = None


@dataclass(frozen=True)
class RawValueDifference:
    timestamp: str
    field: str
    first_raw_value: Any
    second_raw_value: Any
    numeric_meaning_same: bool


@dataclass(frozen=True)
class MarketValueDifference:
    timestamp: str
    field: str
    first_value: float
    second_value: float


@dataclass(frozen=True)
class DownloadSnapshot:
    request_status: str
    http_status: int | None
    api_status: str | None
    error_code: str | None
    error_type: str | None
    safe_request_parameters: tuple[tuple[str, Any], ...]
    raw_payload: Any
    raw_record_count: int
    raw_timestamps: tuple[str, ...]
    raw_field_presence: tuple[tuple[str, tuple[str, ...]], ...]
    raw_field_order: tuple[tuple[str, tuple[str, ...]], ...]
    normalized_bars: tuple[MarketBar, ...]
    rth_bars: tuple[MarketBar, ...]
    session_status: str
    duplicate_timestamps: tuple[str, ...]


@dataclass(frozen=True)
class RepeatDownloadResult:
    instrument: RepeatInstrument
    interval: str
    start: str
    end: str
    timezone: str
    first: DownloadSnapshot
    second: DownloadSnapshot
    status: str
    request_parameters_identical: bool
    raw_order_changed: bool
    raw_metadata_status_changed: bool
    raw_record_count_changed: bool
    raw_timestamp_set_changed: bool
    normalized_order_changed: bool
    raw_field_order_changed: bool
    raw_field_presence_changes: tuple[tuple[str, str], ...]
    raw_representation_changes: tuple[RawValueDifference, ...]
    differences: tuple[MarketValueDifference, ...]
    missing_from_first: tuple[str, ...]
    missing_from_second: tuple[str, ...]
    duplicates_first: tuple[str, ...]
    duplicates_second: tuple[str, ...]
    not_executed_reason: str | None


@dataclass(frozen=True)
class DQ12Report:
    results: tuple[RepeatDownloadResult, ...]
    total_tests: int
    pass_count: int
    difference_count: int
    provider_limitation_count: int
    not_executed_count: int


TEST_PERIOD_START = "2026-08-03T00:00:00+00:00"
TEST_PERIOD_END = "2026-08-04T00:00:00+00:00"
TEST_INSTRUMENTS = (
    RepeatInstrument("AAPL", "AAPL", "United States", "US"),
    RepeatInstrument("TSLA", "TSLA", "United States", "US"),
    RepeatInstrument("Investor B", "INVE.B", "Sweden", None),
    RepeatInstrument(
        "Volvo AB B candidate",
        "VOLV.B",
        "Sweden",
        None,
        "DQ-11 search found Volvo B ambiguous with Volvo Car B; this is the explicit VOLV.B candidate only.",
    ),
)


def _raw_rows(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        return ()
    return tuple(deepcopy(row) for row in payload["values"] if isinstance(row, dict))


def _safe_request_params(params: Any) -> tuple[tuple[str, Any], ...]:
    if not isinstance(params, dict):
        return ()
    return tuple(sorted((key, value) for key, value in params.items() if key.lower() not in {"apikey", "api_key", "api_token"}))


def _status_from_capture(
    http_status: int | None,
    payload: Any,
    error_type: str | None,
    normalized_bars: tuple[MarketBar, ...],
) -> tuple[str, str | None, str | None]:
    raw_api_status = payload.get("status") if isinstance(payload, dict) else None
    api_status = raw_api_status if raw_api_status in {"ok", "error"} else (
        "other" if raw_api_status is not None else None
    )
    code = payload.get("code") if isinstance(payload, dict) else None
    error_code = str(code) if isinstance(code, int) or (isinstance(code, str) and code.isdigit()) else None
    if http_status in (402, 403, 429) or error_code in {"402", "403", "429"}:
        return "PROVIDER_LIMITATION", api_status, error_code or str(http_status)
    if error_type in {"Timeout", "ConnectionError", "RequestException"}:
        return "PROVIDER_LIMITATION", api_status, error_code
    if error_type == "HTTPError":
        if http_status == 408 or (http_status is not None and http_status >= 500):
            return "PROVIDER_LIMITATION", api_status, error_code or str(http_status)
        return "UNAVAILABLE", api_status, error_code or (str(http_status) if http_status is not None else None)
    if api_status == "error":
        message = str(payload.get("message", "")).lower()
        if any(term in message for term in ("rate limit", "credit", "subscription", "entitlement", "plan")):
            return "PROVIDER_LIMITATION", api_status, error_code
        return "UNAVAILABLE", api_status, error_code
    if error_type == "ProviderError":
        return "PARTIAL", api_status, error_code
    if error_type:
        return "UNAVAILABLE", api_status, error_code
    if http_status is None or http_status >= 400:
        return "UNAVAILABLE", api_status, error_code
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        return "PARTIAL", api_status, error_code
    if not normalized_bars:
        return "UNAVAILABLE", api_status, error_code
    return "AVAILABLE", api_status, error_code


def _capture_one(
    provider: TwelveDataProvider,
    instrument: RepeatInstrument,
    start,
    end,
    request_get: Callable[..., Any] | None = None,
) -> DownloadSnapshot:
    capture: dict[str, Any] = {
        "http_status": None,
        "payload": None,
        "params": (),
        "error_type": None,
    }
    original_get = request_get or requests.get

    def capturing_get(url: str, **kwargs: Any) -> Any:
        capture["params"] = _safe_request_params(kwargs.get("params"))
        try:
            response = original_get(url, **kwargs)
        except requests.RequestException as error:
            capture["http_status"] = error.response.status_code if error.response is not None else None
            capture["error_type"] = type(error).__name__
            raise
        capture["http_status"] = getattr(response, "status_code", None)
        try:
            capture["payload"] = deepcopy(response.json())
        except (TypeError, ValueError):
            capture["payload"] = None
        return response

    normalized: tuple[MarketBar, ...] = ()
    try:
        with patch("src.providers.requests.get", side_effect=capturing_get):
            normalized = tuple(provider.get_1h(instrument.provider_symbol, start, end))
    except Exception as error:
        capture["error_type"] = type(error).__name__

    payload = capture["payload"]
    rows = _raw_rows(payload)
    raw_timestamps = tuple(
        str(row.get("datetime", ""))
        for row in rows
    )
    raw_field_presence = tuple(
        sorted(
            (str(row.get("datetime", "")), tuple(sorted(row.keys())))
            for row in rows
        )
    )
    raw_field_order = tuple(
        (str(row.get("datetime", "")), tuple(row.keys()))
        for row in rows
    )
    if instrument.session_classifier == "US":
        rth_bars = tuple(
            bar for bar in normalized
            if classify_us_timestamp(bar.timestamp).is_rth
        )
        session_status = "AVAILABLE"
    else:
        rth_bars = ()
        session_status = "NOT_EXECUTED"

    counts = Counter(bar.timestamp.isoformat() for bar in rth_bars)
    duplicates = tuple(sorted(timestamp for timestamp, count in counts.items() if count > 1))
    request_status, api_status, error_code = _status_from_capture(
        capture["http_status"], payload, capture["error_type"], normalized
    )
    return DownloadSnapshot(
        request_status=request_status,
        http_status=capture["http_status"],
        api_status=api_status,
        error_code=error_code,
        error_type=capture["error_type"],
        safe_request_parameters=capture["params"],
        raw_payload=payload,
        raw_record_count=len(rows),
        raw_timestamps=raw_timestamps,
        raw_field_presence=raw_field_presence,
        raw_field_order=raw_field_order,
        normalized_bars=normalized,
        rth_bars=rth_bars,
        session_status=session_status,
        duplicate_timestamps=duplicates,
    )


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _compare_downloads(
    instrument: RepeatInstrument,
    first: DownloadSnapshot,
    second: DownloadSnapshot,
) -> RepeatDownloadResult:
    params_identical = first.safe_request_parameters == second.safe_request_parameters
    raw_order_changed = first.raw_timestamps != second.raw_timestamps
    raw_status_changed = (first.http_status, first.api_status) != (second.http_status, second.api_status)
    raw_count_changed = first.raw_record_count != second.raw_record_count
    first_by_time = {bar.timestamp: bar for bar in first.rth_bars}
    second_by_time = {bar.timestamp: bar for bar in second.rth_bars}
    first_times = set(first_by_time)
    second_times = set(second_by_time)
    missing_from_first = tuple(timestamp.isoformat() for timestamp in sorted(second_times - first_times))
    missing_from_second = tuple(timestamp.isoformat() for timestamp in sorted(first_times - second_times))
    differences: list[MarketValueDifference] = []
    raw_changes: list[RawValueDifference] = []
    first_raw_by_time = _raw_rows_by_utc(first.raw_payload)
    second_raw_by_time = _raw_rows_by_utc(second.raw_payload)
    field_presence_changes: list[tuple[str, str]] = []
    raw_field_order_changed = False

    for timestamp in set(first_raw_by_time) & set(second_raw_by_time):
        first_row = first_raw_by_time[timestamp]
        second_row = second_raw_by_time[timestamp]
        field_presence_changes.extend(
            (timestamp.isoformat(), field)
            for field in sorted(set(first_row) ^ set(second_row))
        )
        if tuple(first_row) != tuple(second_row):
            raw_field_order_changed = True

    for timestamp in sorted(first_times & second_times):
        left = first_by_time[timestamp]
        right = second_by_time[timestamp]
        for field in FIELDS:
            left_value = getattr(left, field)
            right_value = getattr(right, field)
            if left_value != right_value:
                differences.append(
                    MarketValueDifference(timestamp.isoformat(), field, left_value, right_value)
                )
            left_raw = first_raw_by_time.get(timestamp, {})
            right_raw = second_raw_by_time.get(timestamp, {})
            if (field not in left_raw) != (field not in right_raw):
                field_presence_changes.append((timestamp.isoformat(), field))
            if field in left_raw and field in right_raw and left_raw[field] != right_raw[field]:
                numeric_equal = (
                    _as_decimal(left_raw[field]) is not None
                    and _as_decimal(left_raw[field]) == _as_decimal(right_raw[field])
                )
                raw_changes.append(
                    RawValueDifference(
                        timestamp.isoformat(), field, left_raw[field], right_raw[field], numeric_equal
                    )
                )

    if not params_identical:
        status = "NOT_EXECUTED"
        reason = "The two requests did not use identical provider parameters."
    elif first.request_status == "PROVIDER_LIMITATION" or second.request_status == "PROVIDER_LIMITATION":
        status = "PROVIDER_LIMITATION"
        reason = "A provider request failed or was limited; market-data stability was not compared."
    elif first.request_status != "AVAILABLE" or second.request_status != "AVAILABLE":
        status = "NOT_EXECUTED"
        reason = "A request was unavailable or partial; market-data stability was not compared."
    elif instrument.session_classifier != "US":
        status = "NOT_EXECUTED"
        reason = "The existing canonical RTH classifier is US-only; no Sweden session rule was introduced."
    elif first.duplicate_timestamps or second.duplicate_timestamps:
        status = "DIFFERENCE_DETECTED"
        reason = "Duplicate canonical RTH timestamps occurred in at least one download."
    elif (
        missing_from_first
        or missing_from_second
        or differences
        or any(not change.numeric_meaning_same for change in raw_changes)
    ):
        status = "DIFFERENCE_DETECTED"
        reason = None
    else:
        status = "PASS"
        reason = None

    return RepeatDownloadResult(
        instrument=instrument,
        interval="1h",
        start=TEST_PERIOD_START,
        end=TEST_PERIOD_END,
        timezone="UTC",
        first=first,
        second=second,
        status=status,
        request_parameters_identical=params_identical,
        raw_order_changed=raw_order_changed,
        raw_metadata_status_changed=raw_status_changed,
        raw_record_count_changed=raw_count_changed,
        raw_timestamp_set_changed=set(first.raw_timestamps) != set(second.raw_timestamps),
        normalized_order_changed=(
            tuple(bar.timestamp for bar in first.rth_bars)
            != tuple(bar.timestamp for bar in second.rth_bars)
        ),
        raw_field_order_changed=raw_field_order_changed,
        raw_field_presence_changes=tuple(sorted(set(field_presence_changes))),
        raw_representation_changes=tuple(raw_changes),
        differences=tuple(differences),
        missing_from_first=missing_from_first,
        missing_from_second=missing_from_second,
        duplicates_first=first.duplicate_timestamps,
        duplicates_second=second.duplicate_timestamps,
        not_executed_reason=reason,
    )


def _raw_rows_by_utc(payload: Any) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        return result
    for row in payload["values"]:
        if not isinstance(row, dict) or not isinstance(row.get("datetime"), str):
            continue
        try:
            timestamp = datetime.fromisoformat(row["datetime"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            result[timestamp.astimezone(timezone.utc)] = row
        except ValueError:
            continue
    return result


def run_repeat_downloads(
    provider: TwelveDataProvider,
    instruments: tuple[RepeatInstrument, ...] = TEST_INSTRUMENTS,
    *,
    request_get: Callable[..., Any] | None = None,
) -> DQ12Report:
    start = datetime.fromisoformat(TEST_PERIOD_START)
    end = datetime.fromisoformat(TEST_PERIOD_END)
    results = []
    for instrument in instruments:
        first = _capture_one(provider, instrument, start, end, request_get)
        second = _capture_one(provider, instrument, start, end, request_get)
        results.append(_compare_downloads(instrument, first, second))
    return DQ12Report(
        results=tuple(results),
        total_tests=len(results),
        pass_count=sum(result.status == "PASS" for result in results),
        difference_count=sum(result.status == "DIFFERENCE_DETECTED" for result in results),
        provider_limitation_count=sum(result.status == "PROVIDER_LIMITATION" for result in results),
        not_executed_count=sum(result.status == "NOT_EXECUTED" for result in results),
    )


def print_dq12_report(report: DQ12Report) -> None:
    for result in report.results:
        print(f"\nInstrument: {result.instrument.requested_instrument}")
        print(f"Provider: Twelve Data; symbol: {result.instrument.provider_symbol}")
        print(f"Interval: {result.interval}; start: {result.start}; end: {result.end}; timezone: {result.timezone}")
        if result.instrument.identity_note:
            print(f"Instrument mapping note: {result.instrument.identity_note}")
        for label, snapshot in (("Request 1", result.first), ("Request 2", result.second)):
            print(
                f"{label}: status={snapshot.request_status}, HTTP={snapshot.http_status}, "
                f"API={snapshot.api_status}, raw records={snapshot.raw_record_count}, "
                f"normalized bars={len(snapshot.normalized_bars)}, "
                f"RTH bars={len(snapshot.rth_bars) if snapshot.session_status == 'AVAILABLE' else 'not classified'}"
            )
            if snapshot.error_code:
                print(f"{label} numeric error code: {snapshot.error_code}")
        print(f"RTH session classification: {result.first.session_status}")
        print(f"Request parameters identical: {result.request_parameters_identical}")
        print(f"Exact match status: {result.status}")
        print(f"Differences: {len(result.differences)}")
        for difference in result.differences:
            print(
                f"  {difference.timestamp} {difference.field}: "
                f"first={difference.first_value!r}, second={difference.second_value!r}"
            )
        print(f"Missing from request 1: {', '.join(result.missing_from_first) or 'none'}")
        print(f"Missing from request 2: {', '.join(result.missing_from_second) or 'none'}")
        print(f"Duplicates request 1: {', '.join(result.duplicates_first) or 'none'}")
        print(f"Duplicates request 2: {', '.join(result.duplicates_second) or 'none'}")
        print(f"Raw record counts changed: {result.raw_record_count_changed}")
        print(f"Request 1 raw timestamps: {', '.join(result.first.raw_timestamps) or 'none'}")
        print(f"Request 2 raw timestamps: {', '.join(result.second.raw_timestamps) or 'none'}")
        print(f"Raw timestamp sets changed: {result.raw_timestamp_set_changed}")
        print(f"Normalized timestamp ordering changed: {result.normalized_order_changed}")
        print(f"Raw response array order changed (ignored if normalized data matches): {result.raw_order_changed}")
        print(f"Raw JSON field-key order changed (ignored as serialization order): {result.raw_field_order_changed}")
        print(f"Raw HTTP/API status changed: {result.raw_metadata_status_changed}")
        print(f"Raw field presence changes: {len(result.raw_field_presence_changes)}")
        print(f"Raw textual value representations changed: {len(result.raw_representation_changes)}")
        for raw_change in result.raw_representation_changes:
            category = "format only" if raw_change.numeric_meaning_same else "numeric change"
            first_raw = (
                str(raw_change.first_raw_value)
                if _as_decimal(raw_change.first_raw_value) is not None
                else "<non-numeric>"
            )
            second_raw = (
                str(raw_change.second_raw_value)
                if _as_decimal(raw_change.second_raw_value) is not None
                else "<non-numeric>"
            )
            print(
                f"  raw {category} {raw_change.timestamp} {raw_change.field}: "
                f"{first_raw} -> {second_raw}"
            )
        if result.not_executed_reason:
            print(f"Reason: {result.not_executed_reason}")
    print("\n=== DQ-12 AGGREGATE ===")
    print(f"TOTAL TESTS: {report.total_tests}")
    print(f"PASS: {report.pass_count}")
    print(f"DIFFERENCE_DETECTED: {report.difference_count}")
    print(f"PROVIDER_LIMITATION: {report.provider_limitation_count}")
    print(f"NOT_EXECUTED: {report.not_executed_count}")