from datetime import datetime
import unittest
from unittest.mock import Mock

from src.instrument_mapping import (
    InstrumentCandidate,
    InstrumentRequest,
    IdentifierValue,
    SYMBOL_CHANGE_HISTORY_STATUS,
    TwelveDataInstrumentProbe,
    resolve_instrument,
)
from src.providers import TwelveDataProvider


def candidate(
    symbol: str,
    *,
    exchange: str = "OMX",
    mic: str = "XSTO",
    name: str = "Investor AB",
    share_class: str | None = None,
    figi: str | None = None,
    country: str = "Sweden",
) -> InstrumentCandidate:
    return InstrumentCandidate(
        provider_symbol=symbol,
        name=name,
        exchange=exchange,
        mic_code=mic,
        instrument_type="Common Stock",
        country=country,
        currency="USD" if country == "United States" else "SEK",
        share_class=share_class,
        identifiers=(
            IdentifierValue("figi_code", figi, "AVAILABLE" if figi else "UNAVAILABLE"),
            IdentifierValue("isin", None, "UNAVAILABLE"),
            IdentifierValue("cusip", None, "UNAVAILABLE"),
        ),
        catalog_status="AVAILABLE",
        historical_fields=(),
    )


class InstrumentMappingTests(unittest.TestCase):
    def test_catalog_uses_exchange_mic_type_and_preserves_search_country(self) -> None:
        request_get = Mock()

        def response_for(url: str, *, params: dict, timeout: int) -> Mock:
            response = Mock()
            response.status_code = 200
            if url.endswith("/symbol_search"):
                response.json.return_value = {
                    "data": [
                        {
                            "symbol": "AAPL",
                            "instrument_name": "Apple Inc.",
                            "exchange": "IEX",
                            "mic_code": "IEXG",
                            "instrument_type": "Common Stock",
                            "country": "United States",
                            "currency": "USD",
                        }
                    ]
                }
            else:
                response.json.return_value = {
                    "data": [
                        {
                            "symbol": "AAPL",
                            "name": "Apple Inc.",
                            "exchange": "IEX",
                            "mic_code": "IEXG",
                            "type": "Common Stock",
                            "figi_code": "FIGI-IEX",
                        }
                    ]
                }
            return response

        request_get.side_effect = response_for
        probe = TwelveDataInstrumentProbe(
            TwelveDataProvider(api_key="test-key"),
            request_get=request_get,
        )
        search = probe._search("AAPL")
        catalog = probe._catalog(search.records[0])
        instrument = candidate(
            "AAPL",
            exchange="IEX",
            mic="IEXG",
            name="Apple Inc.",
            figi="FIGI-IEX",
            country="United States",
        )
        resolution = resolve_instrument(
            InstrumentRequest("AAPL", "AAPL", "United States", "Common Stock", exact_symbol="AAPL"),
            search.status,
            (instrument,),
        )

        catalog_params = request_get.call_args.kwargs["params"]
        self.assertEqual(catalog_params["exchange"], "IEX")
        self.assertEqual(catalog_params["mic_code"], "IEXG")
        self.assertEqual(catalog_params["type"], "Common Stock")
        self.assertNotIn("country", catalog_params)
        self.assertEqual(catalog.status, "AVAILABLE")
        self.assertEqual(resolution.candidates[0].country, "United States")

    def test_unique_instrument_resolution(self) -> None:
        request = InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B")
        resolved = resolve_instrument(request, "AVAILABLE", (candidate("INVE.B", share_class="B"),))

        self.assertEqual(resolved.resolution_status, "AVAILABLE")
        self.assertEqual(resolved.provider_symbol, "INVE.B")
        self.assertEqual(resolved.mic_code, "XSTO")

    def test_same_company_different_share_classes_are_not_collapsed(self) -> None:
        a_share = candidate("INVE.A", share_class="A")
        b_share = candidate("INVE.B", share_class="B")
        resolved_a = resolve_instrument(
            InstrumentRequest("Investor A", "Investor", "Sweden", "Common Stock", "A"),
            "AVAILABLE",
            (a_share, b_share),
        )
        resolved_b = resolve_instrument(
            InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B"),
            "AVAILABLE",
            (a_share, b_share),
        )

        self.assertEqual(resolved_a.provider_symbol, "INVE.A")
        self.assertEqual(resolved_b.provider_symbol, "INVE.B")
        self.assertNotEqual(resolved_a.provider_symbol, resolved_b.provider_symbol)
        self.assertEqual(resolved_a.candidates[0].name, resolved_b.candidates[0].name)

    def test_same_ticker_different_exchange_is_ambiguous_without_exchange_key(self) -> None:
        nasdaq = InstrumentCandidate(
            "AAPL", "Apple Inc.", "NASDAQ", "XNGS", "Common Stock",
            "United States", "USD", None,
            (IdentifierValue("figi_code", "FIGI-NASDAQ", "AVAILABLE"),),
            "AVAILABLE", (),
        )
        iex = InstrumentCandidate(
            "AAPL", "Apple Inc.", "IEX", "IEXG", "Common Stock",
            "United States", "USD", None,
            (IdentifierValue("figi_code", "FIGI-IEX", "AVAILABLE"),),
            "AVAILABLE", (),
        )
        request = InstrumentRequest("AAPL", "AAPL", "United States", "Common Stock", exact_symbol="AAPL")

        result = resolve_instrument(request, "AVAILABLE", (nasdaq, iex))

        self.assertEqual(result.resolution_status, "AMBIGUOUS")
        self.assertEqual(result.ambiguity_status, "AMBIGUOUS")
        self.assertIsNone(result.provider_symbol)
        self.assertEqual({item.mic_code for item in result.candidates}, {"XNGS", "IEXG"})
        self.assertEqual(result.stable_identifier_status, "AVAILABLE")

    def test_stable_identifier_is_preserved_when_available(self) -> None:
        result = resolve_instrument(
            InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B"),
            "AVAILABLE",
            (candidate("INVE.B", share_class="B", figi="FIGI-OBSERVED"),),
        )

        self.assertEqual(result.stable_identifier_status, "AVAILABLE")
        self.assertEqual(result.identifiers[0].value, "FIGI-OBSERVED")

    def test_stable_identifier_unavailable_is_not_guessed(self) -> None:
        result = resolve_instrument(
            InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B"),
            "AVAILABLE",
            (candidate("INVE.B", share_class="B"),),
        )

        self.assertEqual(result.stable_identifier_status, "UNAVAILABLE")
        self.assertIsNone(result.identifiers[0].value)

    def test_catalog_limit_is_not_reported_as_stable_id_absence(self) -> None:
        limited = candidate("INVE.B", share_class="B")
        limited = InstrumentCandidate(
            provider_symbol=limited.provider_symbol,
            name=limited.name,
            exchange=limited.exchange,
            mic_code=limited.mic_code,
            instrument_type=limited.instrument_type,
            country=limited.country,
            currency=limited.currency,
            share_class=limited.share_class,
            identifiers=limited.identifiers,
            catalog_status="PROVIDER_LIMITATION",
            historical_fields=(),
        )

        result = resolve_instrument(
            InstrumentRequest("Investor B", "Investor", "Sweden", "Common Stock", "B"),
            "AVAILABLE",
            (limited,),
        )

        self.assertEqual(result.stable_identifier_status, "PROVIDER_LIMITATION")

    def test_ambiguous_provider_results_remain_separate(self) -> None:
        results = (
            candidate("VOLV.B", name="Volvo AB", share_class="B"),
            candidate("VOLCAR.B", name="Volvo Car AB", share_class="B"),
        )
        result = resolve_instrument(
            InstrumentRequest("Volvo B", "Volvo", "Sweden", "Common Stock", "B"),
            "AVAILABLE",
            results,
        )

        self.assertEqual(result.resolution_status, "AMBIGUOUS")
        self.assertEqual({item.provider_symbol for item in result.candidates}, {"VOLV.B", "VOLCAR.B"})

    def test_symbol_change_history_is_explicitly_unavailable(self) -> None:
        self.assertEqual(SYMBOL_CHANGE_HISTORY_STATUS, "UNAVAILABLE")

    def test_provider_limitation_is_reported_without_error_text(self) -> None:
        def rate_limited(url: str, *, params: dict, timeout: int) -> Mock:
            response = Mock()
            response.status_code = 429
            response.json.return_value = {"status": "error", "code": 429, "message": "private details"}
            return response

        probe = TwelveDataInstrumentProbe(
            TwelveDataProvider(api_key="test-key"),
            request_get=rate_limited,
        )
        result = probe._search("AAPL")

        self.assertEqual(result.status, "PROVIDER_LIMITATION")
        self.assertEqual(result.error_code, "429")
        self.assertNotIn("private details", repr(result))
        self.assertNotIn("test-key", repr(result))


if __name__ == "__main__":
    unittest.main()