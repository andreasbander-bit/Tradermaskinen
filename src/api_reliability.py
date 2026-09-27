from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import ceil
import re
from statistics import mean, median
from time import perf_counter
from typing import Any, Callable
from unittest.mock import patch

import requests

from src.providers import TwelveDataProvider


TEST_PERIOD_START = "2026-08-03T00:00:00+00:00"
TEST_PERIOD_END = "2026-08-04T00:00:00+00:00"
NORMAL_SYMBOLS = ("AAPL", "TSLA", "INVE.B")
BURST_SYMBOL = "AAPL"
DEFAULT_BURST_REQUEST_COUNT = 5
MAX_BURST_REQUEST_COUNT = 10
P95_MINIMUM_SAMPLE_COUNT = 20

OUTCOMES = (
    "SUCCESS",
    "HTTP_ERROR",
    "API_ERROR",
    "TIMEOUT",
    "RATE_LIMITED",
    "PROVIDER_LIMITATION",
    "UNAVAILABLE",
    "NETWORK_ERROR",
    "NOT_EXECUTED",
)
ERROR_OUTCOMES = tuple(outcome for outcome in OUTCOMES if outcome != "SUCCESS")
_METADATA_KEY = re.compile(
    r"rate.?limit|quota|credit|daily.*request|request.*(remaining|used|reset)|"
    r"remaining.*request|limit.*request",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY = re.compile(r"key|token|auth|cookie|url", re.IGNORECASE)
_SAFE_METADATA_VALUE = re.compile(r"^[A-Za-z0-9_.:+/, -]{1,48}$")


@dataclass(frozen=True)
class RequestMetric:
    request_type: str
    symbol: str
    endpoint_category: str
    interval: str
    sequence_number: int | None
    http_status: int | None
    api_status: str | None
    api_code: str | None
    elapsed_ms: float | None
    classification: str
    record_count: int | None
    timeout: bool
    rate_limited: bool
    provider_limit: bool
    rate_limit_metadata: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LatencySummary:
    sample_count: int
    minimum_ms: float | None
    median_ms: float | None
    mean_ms: float | None
    maximum_ms: float | None
    p95_ms: float | None


@dataclass(frozen=True)
class DQ13Report:
    normal_requests: tuple[RequestMetric, ...]
    burst_requests: tuple[RequestMetric, ...]
    normal_latency: LatencySummary
    normal_success_count: int
    normal_failed_count: int
    normal_success_rate: float
    error_counts: tuple[tuple[str, int], ...]
    burst_configured_count: int
    burst_rate_limit_sequence: int | None
    burst_rate_limit_http_status: int | None
    burst_rate_limit_api_code: str | None
    burst_metadata_status: str
    burst_rate_limit_metadata: tuple[tuple[int, str, str], ...]
    burst_skipped_reason: str | None
    retry_policy_tested: str = "NO"


def _safe_api_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if isinstance(code, int) or (isinstance(code, str) and code.isdigit()):
        return str(code)
    return None


def _api_status(payload: Any) -> str | None:
    if not isinstance(payload, dict) or payload.get("status") is None:
        return None
    status = payload.get("status")
    if isinstance(status, str) and status in {"ok", "error"}:
        return status
    return "other"


def _metadata_value(key: Any, value: Any, api_key: str | None) -> tuple[str, str] | None:
    name = str(key).strip().lower()
    if not name or _SENSITIVE_METADATA_KEY.search(name) or not _METADATA_KEY.search(name):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    safe_value = str(value).strip()
    if api_key and safe_value == api_key:
        return None
    if not _SAFE_METADATA_VALUE.fullmatch(safe_value):
        return None
    return name, safe_value


def _rate_limit_metadata(response: Any, payload: Any, api_key: str | None) -> tuple[tuple[str, str], ...]:
    metadata: dict[str, str] = {}
    headers = getattr(response, "headers", {})
    if hasattr(headers, "items"):
        for key, value in headers.items():
            item = _metadata_value(key, value, api_key)
            if item is not None:
                metadata[item[0]] = item[1]

    def inspect_payload(value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key, item_value in value.items():
            item = _metadata_value(key, item_value, api_key)
            if item is not None:
                metadata[item[0]] = item[1]
            elif isinstance(item_value, dict):
                inspect_payload(item_value)

    inspect_payload(payload)
    return tuple(sorted(metadata.items()))


def _classify(
    http_status: int | None,
    payload: Any,
    exception: BaseException | None,
) -> str:
    if isinstance(exception, requests.Timeout):
        return "TIMEOUT"
    if http_status == 429 or _safe_api_code(payload) == "429":
        return "RATE_LIMITED"
    if http_status == 404:
        return "UNAVAILABLE"
    if http_status is not None and 500 <= http_status < 600:
        return "PROVIDER_LIMITATION"
    if http_status is not None and http_status >= 400:
        return "HTTP_ERROR"
    if isinstance(exception, requests.RequestException):
        if isinstance(exception, requests.HTTPError):
            return "HTTP_ERROR"
        return "NETWORK_ERROR"

    if isinstance(payload, dict) and payload.get("status") == "error":
        message = str(payload.get("message", "")).lower()
        if any(term in message for term in ("rate limit", "too many requests")):
            return "RATE_LIMITED"
        if _safe_api_code(payload) in {"402", "403"} or any(
            word in message for word in ("quota", "credit", "subscription", "entitlement")
        ):
            return "PROVIDER_LIMITATION"
        return "API_ERROR"

    if exception is not None:
        return "API_ERROR"
    if isinstance(payload, dict) and isinstance(payload.get("values"), list):
        return "SUCCESS"
    return "API_ERROR"


def _capture_request(
    provider: TwelveDataProvider,
    symbol: str,
    request_type: str,
    sequence_number: int | None,
    request_get: Callable[..., Any] | None,
    clock: Callable[[], float],
) -> RequestMetric:
    capture: dict[str, Any] = {
        "response": None,
        "payload": None,
        "http_status": None,
        "exception": None,
        "elapsed_ms": None,
    }
    original_get = request_get or requests.get

    def capturing_get(url: str, **kwargs: Any) -> Any:
        started = clock()
        try:
            response = original_get(url, **kwargs)
        except requests.RequestException as error:
            capture["exception"] = error
            capture["response"] = error.response
            capture["http_status"] = (
                error.response.status_code if error.response is not None else None
            )
            if error.response is not None:
                try:
                    capture["payload"] = error.response.json()
                except Exception:
                    pass
            raise
        finally:
            capture["elapsed_ms"] = (clock() - started) * 1000

        capture["response"] = response
        capture["http_status"] = getattr(response, "status_code", None)
        try:
            capture["payload"] = response.json()
        except Exception:
            capture["payload"] = None
        return response

    start = datetime.fromisoformat(TEST_PERIOD_START)
    end = datetime.fromisoformat(TEST_PERIOD_END)
    try:
        with patch("src.providers.requests.get", side_effect=capturing_get):
            provider.get_1h(symbol, start, end)
    except Exception as error:
        if capture["exception"] is None:
            capture["exception"] = error

    payload = capture["payload"]
    values = payload.get("values") if isinstance(payload, dict) else None
    record_count = len(values) if isinstance(values, list) else None
    classification = _classify(
        capture["http_status"], payload, capture["exception"]
    )
    response = capture["response"]
    metadata = _rate_limit_metadata(response, payload, provider.api_key)
    return RequestMetric(
        request_type=request_type,
        symbol=symbol,
        endpoint_category="time_series",
        interval="1h",
        sequence_number=sequence_number,
        http_status=capture["http_status"],
        api_status=_api_status(payload),
        api_code=_safe_api_code(payload),
        elapsed_ms=capture["elapsed_ms"],
        classification=classification,
        record_count=record_count,
        timeout=classification == "TIMEOUT",
        rate_limited=classification == "RATE_LIMITED",
        provider_limit=classification in {"RATE_LIMITED", "PROVIDER_LIMITATION"},
        rate_limit_metadata=metadata,
    )


def _not_executed(request_type: str, symbol: str, sequence: int | None) -> RequestMetric:
    return RequestMetric(
        request_type=request_type,
        symbol=symbol,
        endpoint_category="time_series",
        interval="1h",
        sequence_number=sequence,
        http_status=None,
        api_status=None,
        api_code=None,
        elapsed_ms=None,
        classification="NOT_EXECUTED",
        record_count=None,
        timeout=False,
        rate_limited=False,
        provider_limit=False,
        rate_limit_metadata=(),
    )


def _latency_summary(metrics: tuple[RequestMetric, ...]) -> LatencySummary:
    durations = sorted(
        metric.elapsed_ms
        for metric in metrics
        if metric.elapsed_ms is not None
    )
    if not durations:
        return LatencySummary(0, None, None, None, None, None)
    p95 = None
    if len(durations) >= P95_MINIMUM_SAMPLE_COUNT:
        p95 = durations[ceil(0.95 * len(durations)) - 1]
    return LatencySummary(
        sample_count=len(durations),
        minimum_ms=durations[0],
        median_ms=median(durations),
        mean_ms=mean(durations),
        maximum_ms=durations[-1],
        p95_ms=p95,
    )


def run_api_reliability(
    provider: TwelveDataProvider,
    *,
    normal_symbols: tuple[str, ...] = NORMAL_SYMBOLS,
    burst_symbol: str = BURST_SYMBOL,
    burst_request_count: int = DEFAULT_BURST_REQUEST_COUNT,
    request_get: Callable[..., Any] | None = None,
    clock: Callable[[], float] = perf_counter,
) -> DQ13Report:
    if not 0 <= burst_request_count <= MAX_BURST_REQUEST_COUNT:
        raise ValueError(
            f"burst_request_count must be between 0 and {MAX_BURST_REQUEST_COUNT}."
        )
    normal: list[RequestMetric] = []
    burst: list[RequestMetric] = []
    burst_skipped_reason = None
    for sequence, symbol in enumerate(normal_symbols, start=1):
        metric = _capture_request(
            provider, symbol, "normal", sequence, request_get, clock
        )
        normal.append(metric)
        if metric.classification == "RATE_LIMITED":
            for remaining_sequence, remaining_symbol in enumerate(
                normal_symbols[sequence:], start=sequence + 1
            ):
                normal.append(
                    _not_executed("normal", remaining_symbol, remaining_sequence)
                )
            burst_skipped_reason = "A normal request was rate limited."
            break

    if burst_skipped_reason is not None:
        burst.append(_not_executed("burst", burst_symbol, 1))
    else:
        for sequence in range(1, burst_request_count + 1):
            metric = _capture_request(
                provider, burst_symbol, "burst", sequence, request_get, clock
            )
            burst.append(metric)
            if metric.classification == "RATE_LIMITED":
                break

    normal_metrics = tuple(normal)
    burst_metrics = tuple(burst)
    actual_normal = tuple(
        metric for metric in normal_metrics if metric.classification != "NOT_EXECUTED"
    )
    counts = Counter(
        metric.classification for metric in (*normal_metrics, *burst_metrics)
    )
    rate_limited_burst = next(
        (metric for metric in burst_metrics if metric.classification == "RATE_LIMITED"),
        None,
    )
    burst_metadata = tuple(
        sorted(
            (metric.sequence_number or 0, key, value)
            for metric in burst_metrics
            for key, value in metric.rate_limit_metadata
        )
    )
    normal_success_count = sum(
        metric.classification == "SUCCESS" for metric in actual_normal
    )
    normal_failed_count = sum(
        metric.classification != "SUCCESS" for metric in actual_normal
    )
    return DQ13Report(
        normal_requests=normal_metrics,
        burst_requests=burst_metrics,
        normal_latency=_latency_summary(actual_normal),
        normal_success_count=normal_success_count,
        normal_failed_count=normal_failed_count,
        normal_success_rate=(
            normal_success_count / len(actual_normal) if actual_normal else 0.0
        ),
        error_counts=tuple((outcome, counts.get(outcome, 0)) for outcome in ERROR_OUTCOMES),
        burst_configured_count=burst_request_count,
        burst_rate_limit_sequence=(
            rate_limited_burst.sequence_number if rate_limited_burst else None
        ),
        burst_rate_limit_http_status=(
            rate_limited_burst.http_status if rate_limited_burst else None
        ),
        burst_rate_limit_api_code=(
            rate_limited_burst.api_code if rate_limited_burst else None
        ),
        burst_metadata_status="AVAILABLE" if burst_metadata else "UNAVAILABLE",
        burst_rate_limit_metadata=burst_metadata,
        burst_skipped_reason=burst_skipped_reason,
    )


def _format_ms(value: float | None) -> str:
    return "NOT_REPORTED" if value is None else f"{value:.3f}"


def print_dq13_report(report: DQ13Report) -> None:
    print("=== DQ-13 API Reliability / Latency / Rate Limits ===")
    print(f"Provider: Twelve Data")
    print(f"Historical window: {TEST_PERIOD_START} -> {TEST_PERIOD_END}")
    print("Endpoint category: time_series; interval: 1h")
    print("\nNormal requests:")
    for metric in report.normal_requests:
        print(
            f"  {metric.sequence_number}. symbol={metric.symbol} "
            f"outcome={metric.classification} HTTP={metric.http_status} "
            f"API={metric.api_status}/{metric.api_code} "
            f"latency_ms={_format_ms(metric.elapsed_ms)} records={metric.record_count}"
        )
        for key, value in metric.rate_limit_metadata:
            print(f"    metadata {key}: {value}")
    latency = report.normal_latency
    print(f"Normal request count: {len(report.normal_requests) - sum(m.classification == 'NOT_EXECUTED' for m in report.normal_requests)}")
    print(f"Successful requests: {report.normal_success_count}")
    print(f"Failed requests: {report.normal_failed_count}")
    print(f"Success rate: {report.normal_success_rate * 100:.2f}%")
    print(f"Latency sample count: {latency.sample_count}")
    print(f"Latency min/median/mean/max ms: {_format_ms(latency.minimum_ms)}/{_format_ms(latency.median_ms)}/{_format_ms(latency.mean_ms)}/{_format_ms(latency.maximum_ms)}")
    print(f"Latency p95 ms: {_format_ms(latency.p95_ms)}")
    print("\nError counts:")
    for outcome, count in report.error_counts:
        print(f"  {outcome}: {count}")
    print("\nControlled burst:")
    print(f"Configured request maximum: {report.burst_configured_count}")
    print(f"Burst request count: {sum(m.classification != 'NOT_EXECUTED' for m in report.burst_requests)}")
    for metric in report.burst_requests:
        print(
            f"  {metric.sequence_number}. outcome={metric.classification} "
            f"latency_ms={_format_ms(metric.elapsed_ms)} HTTP={metric.http_status} "
            f"API={metric.api_code}"
        )
        for key, value in metric.rate_limit_metadata:
            print(f"    metadata {key}: {value}")
    burst_successes = sum(
        metric.classification == "SUCCESS" for metric in report.burst_requests
    )
    burst_failures = sum(
        metric.classification not in {"SUCCESS", "NOT_EXECUTED"}
        for metric in report.burst_requests
    )
    print(f"Burst successful requests: {burst_successes}")
    print(f"Burst failed requests: {burst_failures}")
    print(f"First rate-limit sequence: {report.burst_rate_limit_sequence}")
    print(f"First rate-limit HTTP status: {report.burst_rate_limit_http_status}")
    print(f"First rate-limit API code: {report.burst_rate_limit_api_code}")
    print(f"Rate-limit metadata: {report.burst_metadata_status}")
    for sequence, key, value in report.burst_rate_limit_metadata:
        print(f"  request {sequence} {key}: {value}")
    if report.burst_skipped_reason:
        print(f"Burst skipped: {report.burst_skipped_reason}")
    print("Retry occurred: NO")
    print(f"RETRY_POLICY_TESTED = {report.retry_policy_tested}")

    observed = sum(metric.classification != "NOT_EXECUTED" for metric in report.normal_requests)
    successful = report.normal_success_count
    print("\nArchitectural finding:")
    if observed:
        print(f"  Observed reliability: {successful}/{observed} normal requests succeeded.")
    else:
        print("  Observed reliability: no normal requests executed.")
    if latency.sample_count:
        print(
            "  Observed latency: "
            f"median {_format_ms(latency.median_ms)} ms, "
            f"range {_format_ms(latency.minimum_ms)}-{_format_ms(latency.maximum_ms)} ms."
        )
    else:
        print("  Observed latency: no timed request samples.")
    if report.burst_rate_limit_sequence is not None:
        print(
            "  Observed rate-limit behavior: detected at burst request "
            f"{report.burst_rate_limit_sequence}; burst stopped without retry."
        )
    elif report.burst_skipped_reason:
        print("  Observed rate-limit behavior: burst not executed after a normal-stage limit.")
    elif report.burst_requests and burst_successes == 0:
        print(
            "  Observed rate-limit behavior: inconclusive; no burst request succeeded, "
            "and no rate-limit response was observed."
        )
    else:
        print("  Observed rate-limit behavior: no rate-limit response in the bounded burst.")
    print(f"  RATE_LIMIT_METADATA = {report.burst_metadata_status}")
    operational_failures = sum(
        count
        for outcome, count in report.error_counts
        if outcome in {
            "HTTP_ERROR",
            "API_ERROR",
            "TIMEOUT",
            "RATE_LIMITED",
            "PROVIDER_LIMITATION",
            "NETWORK_ERROR",
        }
    )
    if operational_failures:
        print(
            "  Future API reliability layer: observed operational failures support "
            "explicit handling; this sample does not justify a retry policy."
        )
    else:
        print(
            "  Future API reliability layer: this sample does not demonstrate "
            "a need; no recovery behavior was introduced."
        )