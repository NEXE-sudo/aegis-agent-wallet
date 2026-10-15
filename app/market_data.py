"""Read-only crypto and equity quotes behind a shared provider interface."""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol

import httpx

AssetClass = Literal["crypto", "equity"]
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_CRYPTO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
_CRYPTO_MAX_AGE = timedelta(hours=2)
_EQUITY_MAX_AGE = timedelta(days=4)
_REQUEST_TIMEOUT_SECONDS = 5.0


class MarketDataError(RuntimeError):
    """An upstream provider failed or returned unusable market data."""


class UnsupportedMarketSymbol(ValueError):
    """The requested symbol is invalid or unsupported by the selected provider."""


@dataclass(frozen=True)
class MarketQuote:
    asset_class: AssetClass
    symbol: str
    price: float
    currency: str
    as_of: datetime
    source: str
    is_stale: bool


class MarketDataProvider(Protocol):
    """Common read-only interface for market-data adapters."""

    def get_quote(self, asset_class: str, symbol: str) -> MarketQuote: ...


def _normalize_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise UnsupportedMarketSymbol("Symbol must be text")
    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise UnsupportedMarketSymbol("Symbol format is invalid")
    return normalized


def _parse_price(value: object) -> float:
    if isinstance(value, bool):
        raise MarketDataError("Market-data provider returned an invalid price")
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketDataError("Market-data provider returned an invalid price") from exc
    if not math.isfinite(price) or price <= 0:
        raise MarketDataError("Market-data provider returned an invalid price")
    return price


def _quote(
    asset_class: AssetClass,
    symbol: str,
    price: object,
    as_of: datetime,
    source: str,
    max_age: timedelta,
) -> MarketQuote:
    if as_of.tzinfo is None:
        raise MarketDataError("Market-data provider returned a timestamp without a timezone")
    now = datetime.now(UTC)
    if as_of > now + timedelta(minutes=5):
        raise MarketDataError("Market-data provider returned a future timestamp")
    return MarketQuote(
        asset_class=asset_class,
        symbol=symbol,
        price=_parse_price(price),
        currency="USD",
        as_of=as_of.astimezone(UTC),
        source=source,
        is_stale=now - as_of.astimezone(UTC) > max_age,
    )


class CoinGeckoProvider:
    """Public CoinGecko simple-price adapter for a small explicit crypto symbol set."""

    source = "coingecko"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def get_quote(self, asset_class: str, symbol: str) -> MarketQuote:
        if asset_class != "crypto":
            raise UnsupportedMarketSymbol("CoinGecko supports crypto quotes only")
        normalized = _normalize_symbol(symbol)
        coin_id = _CRYPTO_IDS.get(normalized)
        if coin_id is None:
            raise UnsupportedMarketSymbol("Supported crypto symbols: BTC, ETH, SOL")
        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": coin_id,
                        "vs_currencies": "usd",
                        "include_last_updated_at": "true",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarketDataError("Crypto market-data provider request failed") from exc
        try:
            item = payload[coin_id]
            price = item["usd"]
            updated = item["last_updated_at"]
            if isinstance(updated, bool) or not isinstance(updated, int):
                raise TypeError
            as_of = datetime.fromtimestamp(updated, tz=UTC)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MarketDataError("Crypto market-data provider returned an invalid response") from exc
        return _quote("crypto", normalized, price, as_of, self.source, _CRYPTO_MAX_AGE)


class StooqProvider:
    """Public Stooq daily-close adapter for US-listed equity symbols."""

    source = "stooq"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def get_quote(self, asset_class: str, symbol: str) -> MarketQuote:
        if asset_class != "equity":
            raise UnsupportedMarketSymbol("Stooq supports equity quotes only")
        normalized = _normalize_symbol(symbol)
        provider_symbol = normalized.replace(".", "-").lower() + ".us"
        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = client.get(
                    "https://stooq.com/q/l/",
                    params={"s": provider_symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
                )
                response.raise_for_status()
                text = response.text
        except httpx.HTTPError as exc:
            raise MarketDataError("Equity market-data provider request failed") from exc
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
            if len(rows) != 1:
                raise ValueError
            row = rows[0]
            if row.get("Symbol", "").upper() != provider_symbol.upper():
                raise ValueError
            price = row["Close"]
            session_date = date.fromisoformat(row["Date"])
            if not row.get("Time") or row["Time"].upper() == "N/D":
                raise ValueError
            as_of = datetime.combine(session_date, time.max, tzinfo=UTC)
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("Equity market-data provider returned an invalid response") from exc
        return _quote("equity", normalized, price, as_of, self.source, _EQUITY_MAX_AGE)


class CompositeMarketDataProvider:
    """Dispatch both asset classes through one provider contract."""

    def __init__(
        self,
        crypto_provider: MarketDataProvider | None = None,
        equity_provider: MarketDataProvider | None = None,
    ) -> None:
        self._crypto = crypto_provider or CoinGeckoProvider()
        self._equity = equity_provider or StooqProvider()

    def get_quote(self, asset_class: str, symbol: str) -> MarketQuote:
        if asset_class == "crypto":
            return self._crypto.get_quote(asset_class, symbol)
        if asset_class == "equity":
            return self._equity.get_quote(asset_class, symbol)
        raise UnsupportedMarketSymbol("Asset class must be 'crypto' or 'equity'")
