from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests

from src.sessions import NEW_YORK


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str


class ProviderError(RuntimeError):
    """Raised when a market-data provider returns an unusable response."""


def _to_unix_timestamp(value: datetime | date) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        utc_value = value.astimezone(timezone.utc)
    else:
        utc_value = datetime.combine(value, time.min, tzinfo=timezone.utc)

    return int(utc_value.timestamp())


class EODHDProvider:
    BASE_URL = "https://eodhd.com/api"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("EODHD_API_KEY")
        if not self.api_key:
            raise ProviderError("EODHD_API_KEY is not configured.")

    def get_1h(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        params = {
            "api_token": self.api_key,
            "interval": "1h",
            "from": _to_unix_timestamp(start),
            "to": _to_unix_timestamp(end),
            "fmt": "json",
        }

        try:
            response = requests.get(
                f"{self.BASE_URL}/intraday/{symbol}",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if exc.response is not None:
                message = (
                    f"EODHD request failed with HTTP "
                    f"{exc.response.status_code}."
                )
            else:
                message = f"EODHD request failed ({type(exc).__name__})."
            raise ProviderError(message) from None

        payload = response.json()

        if not isinstance(payload, list):
            raise ProviderError(
                f"EODHD returned unexpected response for {symbol}: "
                f"{type(payload).__name__}"
            )

        bars: list[MarketBar] = []

        for row in payload:
            try:
                timestamp = datetime.fromtimestamp(
                    int(row["timestamp"]),
                    tz=timezone.utc,
                )

                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        provider="EODHD",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Invalid EODHD bar for {symbol}."
                ) from exc

        return sorted(bars, key=lambda bar: bar.timestamp)


class TwelveDataProvider:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("TWELVE_DATA_API_KEY")
        if not self.api_key:
            raise ProviderError("TWELVE_DATA_API_KEY is not configured.")

    def get_1h(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        params = {
            "symbol": symbol,
            "interval": "1h",
            "start_date": start.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "end_date": end.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "timezone": "UTC",
            "outputsize": 5000,
            "apikey": self.api_key,
        }

        response = requests.get(
            f"{self.BASE_URL}/time_series",
            params=params,
            timeout=30,
        )
        response.raise_for_status()

        payload: dict[str, Any] = response.json()

        if payload.get("status") == "error":
            raise ProviderError(
                f"Twelve Data error for {symbol}: "
                f"{payload.get('message', 'unknown error')}"
            )

        values = payload.get("values")

        if not isinstance(values, list):
            raise ProviderError(
                f"Twelve Data returned unexpected response for {symbol}."
            )

        bars: list[MarketBar] = []

        for row in values:
            try:
                timestamp = datetime.strptime(
                    row["datetime"],
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=timezone.utc)

                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        provider="Twelve Data",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Invalid Twelve Data bar for {symbol}: {row}"
                ) from exc

        return sorted(bars, key=lambda bar: bar.timestamp)

    def get_1m(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        params = {
            "symbol": symbol,
            "interval": "1min",
            "start_date": start.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "end_date": end.astimezone(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "timezone": "UTC",
            "outputsize": 5000,
            "apikey": self.api_key,
        }

        try:
            response = requests.get(
                f"{self.BASE_URL}/time_series",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if exc.response is not None:
                message = (
                    "Twelve Data 1M request failed with HTTP "
                    f"{exc.response.status_code}."
                )
            else:
                message = (
                    "Twelve Data 1M request failed "
                    f"({type(exc).__name__})."
                )
            raise ProviderError(message) from None

        payload: dict[str, Any] = response.json()

        if payload.get("status") == "error":
            raise ProviderError(
                f"Twelve Data 1M request failed with API code "
                f"{payload.get('code', 'unknown')}."
            )

        values = payload.get("values")
        if not isinstance(values, list):
            raise ProviderError(
                f"Twelve Data returned unexpected 1M response for {symbol}."
            )

        bars: list[MarketBar] = []
        for row in values:
            try:
                timestamp = datetime.strptime(
                    row["datetime"],
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=timezone.utc)
                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        provider="Twelve Data",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Invalid Twelve Data 1M bar for {symbol}."
                ) from exc

        return sorted(bars, key=lambda bar: bar.timestamp)

    def get_daily(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBar]:
        params = {
            "symbol": symbol,
            "interval": "1day",
            "start_date": datetime.combine(
                start_date,
                time.min,
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": datetime.combine(
                end_date + timedelta(days=1),
                time.min,
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "America/New_York",
            "outputsize": 5000,
            "apikey": self.api_key,
        }

        try:
            response = requests.get(
                f"{self.BASE_URL}/time_series",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            if exc.response is not None:
                message = (
                    "Twelve Data daily request failed with HTTP "
                    f"{exc.response.status_code}."
                )
            else:
                message = (
                    "Twelve Data daily request failed "
                    f"({type(exc).__name__})."
                )
            raise ProviderError(message) from None

        payload: dict[str, Any] = response.json()

        if payload.get("status") == "error":
            api_code = payload.get("code")
            if not isinstance(api_code, (int, str)) or not str(api_code).isdigit():
                api_code = "unknown"
            raise ProviderError(
                f"Twelve Data daily request failed with API code {api_code}."
            )

        values = payload.get("values")

        if not isinstance(values, list):
            raise ProviderError(
                f"Twelve Data returned unexpected response for {symbol}."
            )

        bars: list[MarketBar] = []

        for row in values:
            try:
                try:
                    local_timestamp = datetime.strptime(
                        row["datetime"],
                        "%Y-%m-%d",
                    )
                except ValueError:
                    local_timestamp = datetime.strptime(
                        row["datetime"],
                        "%Y-%m-%d %H:%M:%S",
                    )

                timestamp = local_timestamp.replace(
                    tzinfo=NEW_YORK
                ).astimezone(timezone.utc)

                bars.append(
                    MarketBar(
                        symbol=symbol,
                        timestamp=timestamp,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        provider="Twelve Data",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Invalid Twelve Data daily bar for {symbol}."
                ) from exc

        return sorted(bars, key=lambda bar: bar.timestamp)