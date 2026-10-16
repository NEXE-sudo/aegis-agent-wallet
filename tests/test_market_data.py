from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.market_data import (
    CoinGeckoProvider,
    CompositeMarketDataProvider,
    MarketDataError,
    MarketQuote,
    StooqProvider,
    UnsupportedMarketSymbol,
)

client = TestClient(main.app)
OWNER_HEADERS = {"Authorization": "Bearer test-owner-token-with-enough-length"}


@pytest.fixture(autouse=True)
def configured_tokens(monkeypatch):
    monkeypatch.setenv("AEGIS_AGENT_TOKEN", "test-agent-token-with-enough-length")
    monkeypatch.setenv("AEGIS_APPROVAL_TOKEN", "test-owner-token-with-enough-length")


def _transport(handler):
    return httpx.MockTransport(handler)


def test_coingecko_quote_uses_allowlisted_coin_id_and_parses_timestamp():
    updated = int(datetime.now(UTC).timestamp())
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"bitcoin": {"usd": 65000.5, "last_updated_at": updated}})

    quote = CoinGeckoProvider(_transport(handler)).get_quote("crypto", " btc ")

    assert quote.asset_class == "crypto"
    assert quote.symbol == "BTC"
    assert quote.price == 65000.5
    assert quote.currency == "USD"
    assert quote.source == "coingecko"
    assert quote.is_stale is False
    assert "ids=bitcoin" in seen["url"]
    assert "include_last_updated_at=true" in seen["url"]


def test_crypto_provider_rejects_unknown_symbols_without_network_access():
    provider = CoinGeckoProvider(_transport(lambda request: pytest.fail("unexpected request")))

    with pytest.raises(UnsupportedMarketSymbol, match="Supported crypto"):
        provider.get_quote("crypto", "DOGE")


def test_crypto_provider_flags_stale_quotes():
    old = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    provider = CoinGeckoProvider(
        _transport(lambda request: httpx.Response(
            200, json={"ethereum": {"usd": 2000, "last_updated_at": old}}
        ))
    )

    assert provider.get_quote("crypto", "ETH").is_stale is True


def test_equity_provider_parses_daily_close_and_normalizes_share_class():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            text="Symbol,Date,Time,Open,High,Low,Close,Volume\nBRK-B.US,2026-10-13,16:00:00,400,410,399,405.25,1234\n",
        )

    quote = StooqProvider(_transport(handler)).get_quote("equity", "brk.b")

    assert quote.asset_class == "equity"
    assert quote.symbol == "BRK.B"
    assert quote.price == 405.25
    assert quote.source == "stooq"
    assert "s=brk-b.us" in seen["url"]


def test_equity_provider_accepts_current_session_date_without_future_timestamp():
    today = datetime.now(UTC).date().isoformat()
    provider = StooqProvider(
        _transport(
            lambda request: httpx.Response(
                200,
                text=(
                    "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                    f"AAPL.US,{today},16:00:00,1,2,1,190,10\n"
                ),
            )
        )
    )

    quote = provider.get_quote("equity", "AAPL")

    assert quote.as_of <= datetime.now(UTC)
    assert quote.is_stale is False


def test_equity_provider_accepts_two_day_old_session_date_as_fresh():
    session_date = datetime.now(UTC).date() - timedelta(days=2)
    provider = StooqProvider(
        _transport(
            lambda request: httpx.Response(
                200,
                text=(
                    "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                    f"AAPL.US,{session_date.isoformat()},16:00:00,1,2,1,190,10\n"
                ),
            )
        )
    )

    quote = provider.get_quote("equity", "AAPL")

    assert quote.as_of.date() == session_date
    assert quote.is_stale is False


def test_equity_provider_rejects_future_session_date():
    future_date = datetime.now(UTC).date() + timedelta(days=1)
    provider = StooqProvider(
        _transport(
            lambda request: httpx.Response(
                200,
                text=(
                    "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
                    f"AAPL.US,{future_date.isoformat()},16:00:00,1,2,1,190,10\n"
                ),
            )
        )
    )

    with pytest.raises(MarketDataError, match="future timestamp"):
        provider.get_quote("equity", "AAPL")


@pytest.mark.parametrize("body", [
    "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n",
    "Symbol,Date,Time,Open,High,Low,Close,Volume\nMSFT.US,2026-10-13,16:00:00,1,2,1,0,10\n",
    "not,csv",
])
def test_equity_provider_rejects_missing_or_invalid_quotes(body):
    provider = StooqProvider(_transport(lambda request: httpx.Response(200, text=body)))

    with pytest.raises(MarketDataError):
        provider.get_quote("equity", "AAPL")


def test_composite_provider_dispatches_asset_classes():
    crypto = CoinGeckoProvider(_transport(lambda request: httpx.Response(
        200, json={"solana": {"usd": 150, "last_updated_at": int(datetime.now(UTC).timestamp())}}
    )))
    equity = StooqProvider(_transport(lambda request: httpx.Response(
        200,
        text="Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-10-13,16:00:00,1,2,1,190,10\n",
    )))
    provider = CompositeMarketDataProvider(crypto, equity)

    assert provider.get_quote("crypto", "SOL").price == 150
    assert provider.get_quote("equity", "AAPL").price == 190

    with pytest.raises(UnsupportedMarketSymbol):
        provider.get_quote("forex", "EURUSD")


def test_market_data_endpoint_requires_owner_token(monkeypatch):
    monkeypatch.setattr(main, "market_data_provider", type("Unused", (), {
        "get_quote": staticmethod(lambda *_: pytest.fail("provider must not be called"))
    })())

    response = client.get("/market-data/crypto/BTC")

    assert response.status_code == 401


def test_market_data_endpoint_returns_normalized_quote(monkeypatch):
    quote = MarketQuote(
        asset_class="crypto",
        symbol="BTC",
        price=65000.5,
        currency="USD",
        as_of=datetime.now(UTC),
        source="test",
        is_stale=False,
    )
    monkeypatch.setattr(main, "market_data_provider", type("Stub", (), {
        "get_quote": staticmethod(lambda asset_class, symbol: quote)
    })())

    response = client.get("/market-data/crypto/BTC", headers=OWNER_HEADERS)

    assert response.status_code == 200
    assert response.json()["symbol"] == "BTC"
    assert response.json()["price"] == 65000.5
    assert response.json()["is_stale"] is False


def test_market_data_endpoint_sanitizes_provider_errors(monkeypatch):
    monkeypatch.setattr(main, "market_data_provider", type("Stub", (), {
        "get_quote": staticmethod(lambda *_: (_ for _ in ()).throw(MarketDataError("private upstream detail")))
    })())

    response = client.get("/market-data/equity/AAPL", headers=OWNER_HEADERS)

    assert response.status_code == 502
    assert response.json() == {"detail": "Market-data provider unavailable"}


@pytest.mark.parametrize(
    "price",
    [0, -1, "Infinity", "NaN", True, "not-a-price", None],
)
def test_crypto_provider_rejects_invalid_prices(price):
    updated = int(datetime.now(UTC).timestamp())
    provider = CoinGeckoProvider(
        _transport(
            lambda request: httpx.Response(
                200,
                json={"bitcoin": {"usd": price, "last_updated_at": updated}},
            )
        )
    )

    with pytest.raises(MarketDataError, match="invalid price"):
        provider.get_quote("crypto", "BTC")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"bitcoin": {}},
        {"bitcoin": {"usd": 100}},
        {"bitcoin": {"usd": 100, "last_updated_at": True}},
        {"bitcoin": {"usd": 100, "last_updated_at": "not-a-timestamp"}},
        [],
        None,
    ],
)
def test_crypto_provider_rejects_malformed_responses(payload):
    def handler(request):
        if payload is None:
            return httpx.Response(
                200,
                content=b"null",
                headers={"Content-Type": "application/json"},
            )
        return httpx.Response(200, json=payload)

    provider = CoinGeckoProvider(_transport(handler))

    with pytest.raises(MarketDataError, match="invalid response"):
        provider.get_quote("crypto", "BTC")


def test_crypto_provider_rejects_future_quote_timestamp():
    future = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    provider = CoinGeckoProvider(
        _transport(
            lambda request: httpx.Response(
                200,
                json={"bitcoin": {"usd": 100, "last_updated_at": future}},
            )
        )
    )

    with pytest.raises(MarketDataError, match="future timestamp"):
        provider.get_quote("crypto", "BTC")


@pytest.mark.parametrize("provider_factory", [CoinGeckoProvider, StooqProvider])
def test_market_data_providers_sanitize_upstream_http_failures(provider_factory):
    def handler(request):
        return httpx.Response(503, text="private upstream diagnostic")

    provider = provider_factory(_transport(handler))
    asset_class, symbol = (
        ("crypto", "BTC") if provider_factory is CoinGeckoProvider else ("equity", "AAPL")
    )

    with pytest.raises(MarketDataError, match="provider request failed"):
        provider.get_quote(asset_class, symbol)


@pytest.mark.parametrize("provider_factory", [CoinGeckoProvider, StooqProvider])
def test_market_data_providers_do_not_follow_redirects(provider_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://redirect-target.invalid/collect"},
        )

    provider = provider_factory(_transport(handler))
    asset_class, symbol = (
        ("crypto", "BTC") if provider_factory is CoinGeckoProvider else ("equity", "AAPL")
    )

    with pytest.raises(MarketDataError, match="provider request failed"):
        provider.get_quote(asset_class, symbol)

    assert len(requests) == 1
    assert requests[0].url.host in {"api.coingecko.com", "stooq.com"}
