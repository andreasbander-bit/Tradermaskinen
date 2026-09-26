from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

import requests

from src.providers import TwelveDataProvider


SYMBOL_CHANGE_HISTORY_STATUS = "UNAVAILABLE"
STABLE_FIELDS = ("figi_code", "composite_figi", "share_class_figi", "isin", "cusip")
SEARCH_FIELDS = (
    "symbol", "instrument_name", "exchange", "mic_code", "exchange_timezone",
    "instrument_type", "country", "currency", "figi", "composite_figi",
    "share_class_figi", "isin", "cusip",
)
CATALOG_FIELDS = (
    "symbol", "name", "currency", "exchange", "mic_code", "country", "type",
    "figi_code", "cfi_code", "isin", "cusip", "delisted", "is_delisted",
    "valid_from", "valid_to",
)


@dataclass(frozen=True)
class InstrumentRequest:
    requested: str
    search_term: str
    country: str
    instrument_type: str
    share_class: str | None = None
    exact_symbol: str | None = None


@dataclass(frozen=True)
class IdentifierValue:
    field: str
    value: str | None
    status: str


@dataclass(frozen=True)
class InstrumentCandidate:
    provider_symbol: str
    name: str | None
    exchange: str | None
    mic_code: str | None
    instrument_type: str | None
    country: str | None
    currency: str | None
    share_class: str | None
    identifiers: tuple[IdentifierValue, ...]
    catalog_status: str
    historical_fields: tuple[tuple[str, Any], ...]
    classification_code: str | None = None

    @property
    def key(self) -> tuple[str, ...]:
        return tuple(str(value or "") for value in (
            self.provider_symbol, self.exchange, self.mic_code, self.instrument_type,
            self.country, self.currency, self.share_class,
        ))


@dataclass(frozen=True)
class InstrumentResolution:
    requested: str
    search_term: str
    provider_symbol: str | None
    name: str | None
    exchange: str | None
    mic_code: str | None
    instrument_type: str | None
    country: str | None
    currency: str | None
    share_class: str | None
    identifiers: tuple[IdentifierValue, ...]
    stable_identifier_status: str
    resolution_status: str
    ambiguity_status: str
    candidates: tuple[InstrumentCandidate, ...]


@dataclass(frozen=True)
class EndpointResult:
    status: str
    records: tuple[dict[str, Any], ...]
    error_code: str | None = None


@dataclass(frozen=True)
class CrossListingReport:
    base: EndpointResult
    exchange_filter: EndpointResult
    mic_filter: EndpointResult
    filters_changed_results: bool | None


@dataclass(frozen=True)
class DQ11Report:
    resolutions: tuple[InstrumentResolution, ...]
    cross_listings: CrossListingReport
    symbol_change_history_status: str
    historical_identity_status: str
    historical_identity_reason: str
    docs_claim: str
    mapping_recommendation: str


TEST_UNIVERSE = (
    InstrumentRequest("AAPL", "AAPL", "United States", "Common Stock", exact_symbol="AAPL"),
    InstrumentRequest("TSLA", "TSLA", "United States", "Common Stock", exact_symbol="TSLA"),
    InstrumentRequest("NVDA", "NVDA", "United States", "Common Stock", exact_symbol="NVDA"),
    InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B"),
    InstrumentRequest("Saab B", "Saab", "Sweden", "Common Stock", "B"),
    InstrumentRequest("Volvo B", "Volvo", "Sweden", "Common Stock", "B"),
    InstrumentRequest("Investor A", "Investor", "Sweden", "Common Stock", "A"),
    InstrumentRequest("Saab A", "Saab", "Sweden", "Common Stock", "A"),
    InstrumentRequest("Volvo A", "Volvo", "Sweden", "Common Stock", "A"),
)


def _safe_code(value: Any) -> str | None:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdigit() and len(value) <= 6:
        return value
    return None


def _status(http_code: int | None, code: str | None, message: str = "") -> str:
    if http_code in (402, 403, 429) or code in {"402", "403", "429"}:
        return "PROVIDER_LIMITATION"
    if any(term in message.lower() for term in ("plan", "subscription", "credit limit", "rate limit", "entitlement")):
        return "PROVIDER_LIMITATION"
    return "UNAVAILABLE"


def _share_class(record: dict[str, Any]) -> str | None:
    symbol = record.get("symbol")
    if isinstance(symbol, str):
        match = re.search(r"[.\-]([A-Z])$", symbol.upper())
        if match:
            return match.group(1)
    for field in ("instrument_name", "name"):
        name = record.get(field)
        if isinstance(name, str):
            match = re.search(r"\bclass\s+([A-Z])\b|\(([A-Z])\)$", name, re.I)
            if match:
                return (match.group(1) or match.group(2)).upper()
    return None


def _identifier(field: str, value: Any) -> IdentifierValue:
    if isinstance(value, str) and value.strip().lower() == "request_access_via_add_ons":
        return IdentifierValue(field, None, "PROVIDER_LIMITATION")
    if isinstance(value, str) and value.strip():
        return IdentifierValue(field, value.strip(), "AVAILABLE")
    return IdentifierValue(field, None, "UNAVAILABLE")


def _candidate(search: dict[str, Any], catalog: dict[str, Any] | None, catalog_status: str) -> InstrumentCandidate:
    catalog = catalog or {}
    identifiers = tuple(
        _identifier(field, catalog.get(field, search.get(field)))
        for field in STABLE_FIELDS
    )
    history = tuple(
        (field, catalog[field])
        for field in ("delisted", "is_delisted", "valid_from", "valid_to")
        if field in catalog
    )
    return InstrumentCandidate(
        provider_symbol=str(search.get("symbol", "")),
        name=catalog.get("name") or search.get("instrument_name"),
        exchange=catalog.get("exchange") or search.get("exchange"),
        mic_code=catalog.get("mic_code") or search.get("mic_code"),
        instrument_type=catalog.get("type") or search.get("instrument_type"),
        country=catalog.get("country") or search.get("country"),
        currency=catalog.get("currency") or search.get("currency"),
        share_class=_share_class({**search, **catalog}),
        identifiers=identifiers,
        catalog_status=catalog_status,
        historical_fields=history,
        classification_code=catalog.get("cfi_code"),
    )


def resolve_instrument(
    request: InstrumentRequest,
    search_status: str,
    observed: tuple[InstrumentCandidate, ...],
) -> InstrumentResolution:
    scoped = [
        item for item in observed
        if item.country == request.country
        and item.instrument_type == request.instrument_type
        and (request.exact_symbol is None or item.provider_symbol.upper() == request.exact_symbol.upper())
    ]
    matches = scoped
    if request.share_class is not None:
        matches = [item for item in scoped if item.share_class == request.share_class]
    unique = {item.key: item for item in matches}
    selected_candidates = tuple(unique[key] for key in sorted(unique))
    if not selected_candidates:
        status = search_status if search_status in ("PROVIDER_LIMITATION", "PARTIAL") else "UNAVAILABLE"
    elif len(selected_candidates) > 1:
        status = "AMBIGUOUS"
    elif not all((selected_candidates[0].provider_symbol, selected_candidates[0].exchange, selected_candidates[0].mic_code, selected_candidates[0].instrument_type, selected_candidates[0].country)):
        status = "PARTIAL"
    else:
        status = "AVAILABLE" if search_status == "AVAILABLE" else "PARTIAL"

    ids = tuple(identifier for item in selected_candidates for identifier in item.identifiers)
    candidates_with_ids = sum(
        any(identifier.value is not None for identifier in candidate.identifiers)
        for candidate in selected_candidates
    )
    if selected_candidates and candidates_with_ids == len(selected_candidates):
        id_status = "AVAILABLE"
    elif candidates_with_ids:
        id_status = "PARTIAL"
    elif any(
        candidate.catalog_status == "PROVIDER_LIMITATION"
        for candidate in selected_candidates
    ) or any(item.status == "PROVIDER_LIMITATION" for item in ids):
        id_status = "PROVIDER_LIMITATION"
    else:
        id_status = "UNAVAILABLE"
    selected = selected_candidates[0] if len(selected_candidates) == 1 else None
    return InstrumentResolution(
        request.requested, request.search_term,
        selected.provider_symbol if selected else None,
        selected.name if selected else None,
        selected.exchange if selected else None,
        selected.mic_code if selected else None,
        selected.instrument_type if selected else None,
        selected.country if selected else None,
        selected.currency if selected else None,
        selected.share_class if selected else request.share_class,
        selected.identifiers if selected else ids,
        id_status,
        status,
        "AMBIGUOUS" if status == "AMBIGUOUS" else "NOT_AMBIGUOUS",
        tuple(sorted(scoped, key=lambda item: item.key)),
    )


class TwelveDataInstrumentProbe:
    def __init__(self, provider: TwelveDataProvider, *, request_get: Callable[..., Any] | None = None) -> None:
        self.provider = provider
        self.request_get = request_get or requests.get
        self.search_cache: dict[str, EndpointResult] = {}
        self.catalog_cache: dict[tuple[str, ...], EndpointResult] = {}

    def _get(self, endpoint: str, params: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            response = self.request_get(f"{self.provider.BASE_URL}/{endpoint}", params=params, timeout=30)
        except requests.RequestException as error:
            code = error.response.status_code if error.response is not None else None
            return _status(code, None), None, str(code) if code is not None else None
        except Exception:
            return "UNAVAILABLE", None, None
        http_code = getattr(response, "status_code", None)
        if http_code is not None and http_code >= 400:
            return _status(http_code, None), None, str(http_code)
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return "PARTIAL", None, None
        if not isinstance(payload, dict):
            return "PARTIAL", None, None
        if payload.get("status") == "error":
            code = _safe_code(payload.get("code"))
            return _status(http_code, code, str(payload.get("message", ""))), None, code
        return "AVAILABLE", payload, None

    def _search(self, term: str) -> EndpointResult:
        if term in self.search_cache:
            return self.search_cache[term]
        status, payload, code = self._get("symbol_search", {"symbol": term, "outputsize": 120, "apikey": self.provider.api_key})
        rows = payload.get("data") if payload else None
        if not isinstance(rows, list):
            result = EndpointResult(status if payload is None else "PARTIAL", (), code)
        else:
            records = tuple(
                {field: row[field] for field in SEARCH_FIELDS if field in row}
                for row in rows if isinstance(row, dict)
            )
            result = EndpointResult("AVAILABLE" if len(records) == len(rows) else "PARTIAL", records, None)
        self.search_cache[term] = result
        return result

    def _catalog(self, record: dict[str, Any]) -> EndpointResult:
        key = tuple(str(record.get(field, "")) for field in ("symbol", "exchange", "mic_code", "instrument_type"))
        if key in self.catalog_cache:
            return self.catalog_cache[key]
        params: dict[str, Any] = {
            "symbol": record.get("symbol"),
            "include_delisted": "true",
            "outputsize": 50,
            "apikey": self.provider.api_key,
        }
        for source, target in (("exchange", "exchange"), ("mic_code", "mic_code"), ("instrument_type", "type")):
            if record.get(source):
                params[target] = record[source]
        status, payload, code = self._get("stocks", params)
        rows = payload.get("data") if payload else None
        if not isinstance(rows, list):
            result = EndpointResult(status if payload is None else "PARTIAL", (), code)
        else:
            records = tuple(
                {field: row[field] for field in CATALOG_FIELDS if field in row}
                for row in rows
                if isinstance(row, dict)
                and row.get("symbol") == record.get("symbol")
                and row.get("mic_code") == record.get("mic_code")
            )
            result = EndpointResult("AVAILABLE" if records else "PARTIAL", records, None)
        self.catalog_cache[key] = result
        return result

    def _cross(self, params: dict[str, str]) -> EndpointResult:
        status, payload, code = self._get("cross_listings", {**params, "apikey": self.provider.api_key})
        data = payload.get("result") if payload else None
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return EndpointResult(status if payload is None else "PARTIAL", (), code)
        records = tuple(
            {field: row[field] for field in ("symbol", "name", "exchange", "mic_code") if field in row}
            for row in rows if isinstance(row, dict)
        )
        return EndpointResult("AVAILABLE" if len(records) == len(rows) else "PARTIAL", records, None)

    def run(self) -> DQ11Report:
        self.search_cache.clear()
        self.catalog_cache.clear()
        resolutions: list[InstrumentResolution] = []
        for request in TEST_UNIVERSE:
            search = self._search(request.search_term)
            scoped_rows = [
                row for row in search.records
                if row.get("country") == request.country
                and row.get("instrument_type") == request.instrument_type
                and (request.exact_symbol is None or str(row.get("symbol", "")).upper() == request.exact_symbol.upper())
            ]
            candidates: list[InstrumentCandidate] = []
            for row in scoped_rows:
                catalog = self._catalog(row)
                catalog_row = next(
                    (item for item in catalog.records if item.get("symbol") == row.get("symbol") and item.get("mic_code") == row.get("mic_code")),
                    None,
                )
                candidates.append(_candidate(row, catalog_row, catalog.status))
            resolutions.append(resolve_instrument(request, search.status, tuple(candidates)))

        cross = (
            self._cross({"symbol": "AAPL"}),
            self._cross({"symbol": "AAPL", "exchange": "NASDAQ"}),
            self._cross({"symbol": "AAPL", "mic_code": "XNGS"}),
        )
        base_set = {_cross_key(record) for record in cross[0].records}
        filters_changed = (
            any({_cross_key(record) for record in result.records} != base_set for result in cross[1:])
            if all(result.status == "AVAILABLE" for result in cross)
            else None
        )
        historical_fields = any(
            _has_historical_identity(candidate)
            for result in resolutions
            for candidate in result.candidates
        )
        catalog_limited = any(
            candidate.catalog_status == "PROVIDER_LIMITATION"
            for result in resolutions
            for candidate in result.candidates
        )
        historical_reason = (
            "The asset catalog returned a positive delisted/validity field."
            if historical_fields
            else (
                "include_delisted=true was queried, but no positive delisted or "
                "valid_from/valid_to field was observed in successful controlled "
                "records. "
                + ("Some catalog requests were provider-limited, so absence is not established." if catalog_limited else "This does not prove no historical identities exist.")
            )
        )
        return DQ11Report(
            tuple(resolutions),
            CrossListingReport(*cross, filters_changed),
            SYMBOL_CHANGE_HISTORY_STATUS,
            "AVAILABLE" if historical_fields else "PARTIAL",
            historical_reason,
            "Twelve Data documents /symbol_search, /stocks with include_delisted and exchange/MIC/type filters, and /cross_listings. /stocks lists FIGI/CFI and ISIN/CUSIP fields; ISIN/CUSIP can require add-on access.",
            "Never use ticker or company name as instrument_id. Preserve provider symbol, exchange, MIC, type, country, currency and observed share class as a time-bounded provider alias. Store FIGI/ISIN/CUSIP separately only when returned. The current provider results do not establish a universal permanent internal identity for every listing.",
        )


def _cross_key(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(record.get(field, "")) for field in ("symbol", "name", "exchange", "mic_code"))


def _has_historical_identity(candidate: InstrumentCandidate) -> bool:
    for field, value in candidate.historical_fields:
        if field in {"valid_from", "valid_to"} and value:
            return True
        if field in {"delisted", "is_delisted"}:
            if value is True or (isinstance(value, str) and value.lower() in {"true", "yes", "delisted"}):
                return True
    return False


def print_dq11_report(report: DQ11Report) -> None:
    print("=== DQ-11 SYMBOL / INSTRUMENT MAPPING ===")
    for result in report.resolutions:
        print(f"\nRequested instrument: {result.requested}")
        print(f"Provider search symbol: {result.search_term}")
        print(f"Resolution status: {result.resolution_status}")
        print(f"Ambiguity status: {result.ambiguity_status}")
        print(f"Stable identifier status: {result.stable_identifier_status}")
        print(f"Symbol-change-history status: {report.symbol_change_history_status}")
        if result.provider_symbol:
            print(f"Provider symbol: {result.provider_symbol}")
            print(f"Resolved name: {result.name or 'UNAVAILABLE'}")
            print(f"Exchange: {result.exchange or 'UNAVAILABLE'}")
            print(f"MIC: {result.mic_code or 'UNAVAILABLE'}")
            print(f"Instrument type: {result.instrument_type or 'UNAVAILABLE'}")
            print(f"Share class: {result.share_class or 'UNAVAILABLE'}")
        print("Candidates:")
        for candidate in result.candidates:
            print(f"  {candidate.provider_symbol} | {candidate.name} | {candidate.exchange} | {candidate.mic_code} | {candidate.instrument_type} | {candidate.country} | {candidate.currency} | class={candidate.share_class or 'UNAVAILABLE'}")
            if candidate.classification_code:
                print(f"    CFI classification code: {candidate.classification_code}")
            for identifier in candidate.identifiers:
                print(f"    {identifier.field}: {identifier.value if identifier.value is not None else identifier.status}")
            if candidate.catalog_status != "AVAILABLE":
                print(f"    /stocks status: {candidate.catalog_status}")
        if not result.candidates:
            print("  no matching provider records")

    print("\nAAPL cross-listing probes:")
    cross = report.cross_listings
    for label, result in (("unfiltered", cross.base), ("exchange=NASDAQ", cross.exchange_filter), ("mic_code=XNGS", cross.mic_filter)):
        print(f"{label}: {result.status}, records={len(result.records)}")
        for record in result.records:
            print(f"  {record.get('symbol')} | {record.get('name')} | {record.get('exchange')} | {record.get('mic_code')}")
    print("Cross-listing filters changed results:", cross.filters_changed_results)
    print("SYMBOL_CHANGE_HISTORY =", report.symbol_change_history_status)
    print("Historical/delisted identity status:", report.historical_identity_status)
    print("Historical/delisted identity note:", report.historical_identity_reason)
    print("Provider documentation:", report.docs_claim)
    print("Minimum observed listing tuple: provider + returned symbol + exchange + MIC + type + country + currency + share class")
    print("Internal mapping recommendation:", report.mapping_recommendation)