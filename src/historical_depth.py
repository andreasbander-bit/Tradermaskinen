from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

import requests

from src.providers import MarketBar, TwelveDataProvider
from src.sessions import NEW_YORK, classify_us_timestamp


DOCUMENTED_MAX_OUTPUTSIZE = 5000
DEFAULT_CHUNK_DAYS = 365
DEFAULT_CLAIM_BUFFER_DAYS = 365


@dataclass(frozen=True)
class HistoricalBar:
    raw_timestamp: str
    timestamp_utc: datetime
    bar: MarketBar


@dataclass(frozen=True)
class HistoricalPeriod:
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class HistoricalRequestIssue:
    endpoint: str
    period: HistoricalPeriod | None
    kind: str
    code: str | None
    provider_limitation: bool


@dataclass(frozen=True)
class YearBarCount:
    year: int
    bars: int


@dataclass(frozen=True)
class HistoricalDepthReport:
    status: str
    provider_documentation_claim_raw: str | None
    provider_documentation_claim_utc: datetime | None
    documented_max_outputsize: int
    probe_start_utc: datetime | None
    probe_end_utc: datetime
    earliest_raw_timestamp: str | None
    earliest_timestamp_utc: datetime | None
    latest_raw_timestamp: str | None
    latest_timestamp_utc: datetime | None
    calendar_span_days: float | None
    trading_days: int
    one_hour_rth_bars: int
    bars_per_calendar_year: tuple[YearBarCount, ...]
    empty_periods: tuple[HistoricalPeriod, ...]
    request_issues: tuple[HistoricalRequestIssue, ...]
    duplicate_bars_removed: int
    non_rth_bars_excluded: int
    requests_made: int


def _parse_timestamp(raw_timestamp: str) -> datetime:
    parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_timestamp(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iter_chunks(
    start: datetime,
    end: datetime,
    chunk_days: int,
) -> Iterable[HistoricalPeriod]:
    cursor = start
    chunk_delta = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + chunk_delta, end)
        yield HistoricalPeriod(cursor, chunk_end)
        cursor = chunk_end
    if cursor == end and start == end:
        yield HistoricalPeriod(start, end)


class TwelveDataHistoricalProbe:
    """Probe retrievable 1H RTH history in bounded date-range requests."""

    def __init__(
        self,
        provider: TwelveDataProvider,
        *,
        chunk_days: int = DEFAULT_CHUNK_DAYS,
        claim_buffer_days: int = DEFAULT_CLAIM_BUFFER_DAYS,
        max_outputsize: int = DOCUMENTED_MAX_OUTPUTSIZE,
        request_get: Callable[..., Any] | None = None,
    ) -> None:
        if chunk_days <= 0 or claim_buffer_days < 0 or max_outputsize <= 0:
            raise ValueError("Probe chunk and outputsize settings must be positive.")
        self.provider = provider
        self.chunk_days = chunk_days
        self.claim_buffer_days = claim_buffer_days
        self.max_outputsize = max_outputsize
        self.request_get = request_get or requests.get
        self.request_issues: list[HistoricalRequestIssue] = []
        self.requests_made = 0

    def _request_payload(
        self,
        endpoint: str,
        params: dict[str, Any],
        period: HistoricalPeriod | None = None,
    ) -> dict[str, Any] | None:
        self.requests_made += 1
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
            limitation = status_code in (402, 403, 429)
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind="HTTP_ERROR",
                    code=str(status_code) if status_code is not None else None,
                    provider_limitation=limitation,
                )
            )
            return None
        except Exception as error:
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind=type(error).__name__,
                    code=None,
                    provider_limitation=False,
                )
            )
            return None

        status_code = getattr(response, "status_code", None)
        if status_code is not None and status_code >= 400:
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind="HTTP_ERROR",
                    code=str(status_code),
                    provider_limitation=status_code in (402, 403, 429),
                )
            )
            return None

        try:
            payload = response.json()
        except (TypeError, ValueError):
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind="INVALID_JSON",
                    code=None,
                    provider_limitation=False,
                )
            )
            return None

        if not isinstance(payload, dict):
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind="INVALID_RESPONSE",
                    code=None,
                    provider_limitation=False,
                )
            )
            return None

        if payload.get("status") == "error":
            raw_code = payload.get("code")
            if isinstance(raw_code, int):
                code = str(raw_code)
            elif isinstance(raw_code, str) and raw_code.isdigit() and len(raw_code) <= 6:
                code = raw_code
            else:
                code = None
            message = str(payload.get("message", "")).lower()
            limitation = (
                code in {"402", "403", "429"}
                or any(
                    term in message
                    for term in ("limit", "credits", "entitlement", "subscription")
                )
            )
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint=endpoint,
                    period=period,
                    kind="PROVIDER_LIMITATION" if limitation else "API_ERROR",
                    code=code,
                    provider_limitation=limitation,
                )
            )
            return None

        return payload

    def _get_documentation_claim(self, symbol: str) -> tuple[str | None, datetime | None]:
        payload = self._request_payload(
            "earliest_timestamp",
            {
                "symbol": symbol,
                "interval": "1h",
                "timezone": "UTC",
                "apikey": self.provider.api_key,
            },
        )
        if payload is None:
            return None, None

        raw_timestamp = payload.get("datetime")
        if not isinstance(raw_timestamp, str):
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint="earliest_timestamp",
                    period=None,
                    kind="MISSING_CLAIM_TIMESTAMP",
                    code=None,
                    provider_limitation=False,
                )
            )
            return None, None
        try:
            return raw_timestamp, _parse_timestamp(raw_timestamp)
        except ValueError:
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint="earliest_timestamp",
                    period=None,
                    kind="INVALID_CLAIM_TIMESTAMP",
                    code=None,
                    provider_limitation=False,
                )
            )
            return raw_timestamp, None

    def _fetch_chunk(
        self,
        symbol: str,
        period: HistoricalPeriod,
    ) -> list[tuple[HistoricalPeriod, list[dict[str, Any]]]]:
        payload = self._request_payload(
            "time_series",
            {
                "symbol": symbol,
                "interval": "1h",
                "start_date": _format_timestamp(period.start_utc),
                "end_date": _format_timestamp(period.end_utc),
                "timezone": "UTC",
                "outputsize": self.max_outputsize,
                "apikey": self.provider.api_key,
            },
            period,
        )
        if payload is None:
            return [(period, [])]

        values = payload.get("values")
        if not isinstance(values, list):
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint="time_series",
                    period=period,
                    kind="MISSING_VALUES",
                    code=None,
                    provider_limitation=False,
                )
            )
            return [(period, [])]

        if len(values) < self.max_outputsize:
            return [(period, values)]

        self.request_issues.append(
            HistoricalRequestIssue(
                endpoint="time_series",
                period=period,
                kind="OUTPUTSIZE_CAP_SPLIT",
                code=str(self.max_outputsize),
                provider_limitation=False,
            )
        )
        duration = period.end_utc - period.start_utc
        if duration <= timedelta(seconds=1):
            self.request_issues.append(
                HistoricalRequestIssue(
                    endpoint="time_series",
                    period=period,
                    kind="OUTPUTSIZE_CAP_UNSPLITTABLE",
                    code=str(self.max_outputsize),
                    provider_limitation=True,
                )
            )
            return [(period, values)]

        midpoint = period.start_utc + duration / 2
        return self._fetch_chunk(
            symbol,
            HistoricalPeriod(period.start_utc, midpoint),
        ) + self._fetch_chunk(
            symbol,
            HistoricalPeriod(midpoint, period.end_utc),
        )

    def run(
        self,
        symbol: str,
        as_of: datetime,
    ) -> HistoricalDepthReport:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware.")
        as_of_utc = as_of.astimezone(timezone.utc)
        self.request_issues = []
        self.requests_made = 0

        claim_raw, claim_utc = self._get_documentation_claim(symbol)
        if claim_utc is None:
            status = (
                "PROVIDER_LIMITATION"
                if any(issue.provider_limitation for issue in self.request_issues)
                else "UNAVAILABLE"
            )
            return self._make_report(
                status,
                claim_raw,
                claim_utc,
                None,
                as_of_utc,
                {},
                (),
                0,
                0,
            )

        probe_start = claim_utc - timedelta(days=self.claim_buffer_days)
        chunks = tuple(self._iter_chunks(probe_start, as_of_utc))
        records: dict[datetime, HistoricalBar] = {}
        empty_periods: list[HistoricalPeriod] = []
        duplicate_bars_removed = 0
        non_rth_bars_excluded = 0

        for chunk in chunks:
            chunk_has_rth = False
            for leaf_period, values in self._fetch_chunk(symbol, chunk):
                leaf_has_rth = False
                for row in values:
                    try:
                        raw_timestamp = row["datetime"]
                        if not isinstance(raw_timestamp, str):
                            raise ValueError("timestamp is not a string")
                        timestamp_utc = _parse_timestamp(raw_timestamp)
                        bar = MarketBar(
                            symbol=symbol,
                            timestamp=timestamp_utc,
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            provider="Twelve Data",
                        )
                    except (KeyError, TypeError, ValueError):
                        self.request_issues.append(
                            HistoricalRequestIssue(
                                endpoint="time_series",
                                period=leaf_period,
                                kind="INVALID_BAR",
                                code=None,
                                provider_limitation=False,
                            )
                        )
                        continue

                    if not classify_us_timestamp(timestamp_utc).is_rth:
                        non_rth_bars_excluded += 1
                        continue

                    leaf_has_rth = True
                    chunk_has_rth = True
                    historical_bar = HistoricalBar(
                        raw_timestamp=raw_timestamp,
                        timestamp_utc=timestamp_utc,
                        bar=bar,
                    )
                    if timestamp_utc in records:
                        duplicate_bars_removed += 1
                    else:
                        records[timestamp_utc] = historical_bar

                if not leaf_has_rth:
                    empty_periods.append(leaf_period)
            if not chunk_has_rth and not any(
                empty.start_utc == chunk.start_utc
                and empty.end_utc == chunk.end_utc
                for empty in empty_periods
            ):
                empty_periods.append(chunk)

        ordered_timestamps = sorted(records)
        internal_empty_periods = []
        if ordered_timestamps:
            earliest = ordered_timestamps[0]
            latest = ordered_timestamps[-1]
            internal_empty_periods = [
                period
                for period in empty_periods
                if period.end_utc > earliest and period.start_utc < latest
            ]
            if self.claim_buffer_days and earliest < claim_utc:
                self.request_issues.append(
                    HistoricalRequestIssue(
                        endpoint="time_series",
                        period=HistoricalPeriod(probe_start, claim_utc),
                        kind="DATA_PRECEDES_PROVIDER_CLAIM",
                        code=None,
                        provider_limitation=False,
                    )
                )

        has_failures = any(
            issue.kind != "OUTPUTSIZE_CAP_SPLIT"
            for issue in self.request_issues
        )
        has_limitations = any(
            issue.provider_limitation for issue in self.request_issues
        )
        empty_after_claim = [
            period
            for period in empty_periods
            if period.end_utc > claim_utc
        ]
        if has_limitations:
            status = "PROVIDER_LIMITATION"
        elif not records:
            status = "UNAVAILABLE"
        elif has_failures or internal_empty_periods or empty_after_claim:
            status = "PARTIAL"
        else:
            status = "AVAILABLE"

        return self._make_report(
            status,
            claim_raw,
            claim_utc,
            probe_start,
            as_of_utc,
            records,
            tuple(empty_periods),
            duplicate_bars_removed,
            non_rth_bars_excluded,
        )

    def _iter_chunks(
        self,
        start: datetime,
        end: datetime,
    ) -> Iterable[HistoricalPeriod]:
        return _iter_chunks(start, end, self.chunk_days)

    def _make_report(
        self,
        status: str,
        claim_raw: str | None,
        claim_utc: datetime | None,
        probe_start: datetime | None,
        probe_end: datetime,
        records: dict[datetime, HistoricalBar],
        empty_periods: tuple[HistoricalPeriod, ...],
        duplicate_bars_removed: int,
        non_rth_bars_excluded: int,
    ) -> HistoricalDepthReport:
        ordered = [records[timestamp] for timestamp in sorted(records)]
        if ordered:
            earliest = ordered[0]
            latest = ordered[-1]
            span_days = (
                latest.timestamp_utc - earliest.timestamp_utc
            ).total_seconds() / 86400
            bars_per_year = CounterYearCount.from_bars(ordered)
            trading_days = len(
                {
                    classify_us_timestamp(bar.timestamp_utc).session_date
                    for bar in ordered
                }
            )
        else:
            earliest = None
            latest = None
            span_days = None
            bars_per_year = ()
            trading_days = 0

        return HistoricalDepthReport(
            status=status,
            provider_documentation_claim_raw=claim_raw,
            provider_documentation_claim_utc=claim_utc,
            documented_max_outputsize=self.max_outputsize,
            probe_start_utc=probe_start,
            probe_end_utc=probe_end,
            earliest_raw_timestamp=(earliest.raw_timestamp if earliest else None),
            earliest_timestamp_utc=(
                earliest.timestamp_utc if earliest else None
            ),
            latest_raw_timestamp=(latest.raw_timestamp if latest else None),
            latest_timestamp_utc=(latest.timestamp_utc if latest else None),
            calendar_span_days=span_days,
            trading_days=trading_days,
            one_hour_rth_bars=len(ordered),
            bars_per_calendar_year=bars_per_year,
            empty_periods=empty_periods,
            request_issues=tuple(self.request_issues),
            duplicate_bars_removed=duplicate_bars_removed,
            non_rth_bars_excluded=non_rth_bars_excluded,
            requests_made=self.requests_made,
        )


class CounterYearCount:
    @staticmethod
    def from_bars(bars: list[HistoricalBar]) -> tuple[YearBarCount, ...]:
        counts: dict[int, int] = {}
        for historical_bar in bars:
            year = historical_bar.timestamp_utc.year
            counts[year] = counts.get(year, 0) + 1
        return tuple(YearBarCount(year, counts[year]) for year in sorted(counts))


def print_historical_depth_report(report: HistoricalDepthReport) -> None:
    print("\n=== DQ-09 HISTORICAL DEPTH ===")
    print(f"Status: {report.status}")
    print("Provider: Twelve Data")
    print("Instrument/timeframe: AAPL 1H RTH")
    print(
        "Provider documentation claim (earliest_timestamp): "
        f"{report.provider_documentation_claim_raw or 'unavailable'}"
    )
    print(
        "Documented time-series outputsize maximum: "
        f"{report.documented_max_outputsize} points per request"
    )
    if report.probe_start_utc is not None:
        print(f"Actual retrieval probe start: {report.probe_start_utc.isoformat()}")
    print(f"Probe end: {report.probe_end_utc.isoformat()}")
    print(
        "Actual earliest retrieved bar: "
        f"{report.earliest_raw_timestamp or 'unavailable'}"
    )
    if report.earliest_timestamp_utc is not None:
        print(f"Earliest timestamp UTC: {report.earliest_timestamp_utc.isoformat()}")
    print(
        "Actual latest retrieved bar: "
        f"{report.latest_raw_timestamp or 'unavailable'}"
    )
    if report.latest_timestamp_utc is not None:
        print(f"Latest timestamp UTC: {report.latest_timestamp_utc.isoformat()}")
    latest_history_undetermined = any(
        issue.endpoint == "time_series"
        and issue.provider_limitation
        and issue.period is not None
        and (
            report.latest_timestamp_utc is None
            or issue.period.end_utc > report.latest_timestamp_utc
        )
        for issue in report.request_issues
    )
    if latest_history_undetermined:
        print(
            "Latest available history: UNDETERMINED; later history chunks "
            "were blocked by a provider limitation."
        )
    print(
        "Total calendar span: "
        + (
            "unavailable"
            if report.calendar_span_days is None
            else f"{report.calendar_span_days:.2f} days"
        )
    )
    print(f"Trading days represented: {report.trading_days}")
    print(f"1H RTH bars: {report.one_hour_rth_bars}")
    print("Bars per calendar year:")
    if report.bars_per_calendar_year:
        for year_count in report.bars_per_calendar_year:
            print(f"  {year_count.year}: {year_count.bars}")
    else:
        print("  none")
    print(f"Time-series requests made: {report.requests_made - 1}")
    print(f"Duplicate bars removed at overlapping boundaries: {report.duplicate_bars_removed}")
    print(f"Non-RTH bars excluded: {report.non_rth_bars_excluded}")
    print("Empty periods:")
    if report.empty_periods:
        for period in report.empty_periods:
            print(
                f"  {period.start_utc.isoformat()} -> "
                f"{period.end_utc.isoformat()}"
            )
    else:
        print("  none")
    print("API request failures or limitations:")
    if report.request_issues:
        for issue in report.request_issues:
            period = (
                ""
                if issue.period is None
                else f" {issue.period.start_utc.isoformat()} -> "
                f"{issue.period.end_utc.isoformat()}"
            )
            print(
                f"  {issue.endpoint}{period}: {issue.kind} "
                f"code={issue.code or 'n/a'} "
                f"provider_limitation={issue.provider_limitation}"
            )
    else:
        print("  none")