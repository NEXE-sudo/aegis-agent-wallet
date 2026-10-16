# Read-only Market Data

Aegis exposes a read-only market-data endpoint using one shared `MarketDataProvider` interface with separate public-provider adapters.

## Endpoint

`GET /market-data/{asset_class}/{symbol}` requires the owner bearer token (`AEGIS_APPROVAL_TOKEN`). The supported asset classes are `crypto` and `equity`.

Examples:

- `GET /market-data/crypto/BTC` — Bitcoin USD spot quote from CoinGecko.
- `GET /market-data/crypto/ETH` — Ether USD spot quote from CoinGecko.
- `GET /market-data/crypto/SOL` — Solana USD spot quote from CoinGecko.
- `GET /market-data/equity/AAPL` — Stooq daily close quote for a US-listed equity.
- `GET /market-data/equity/BRK.B` — Stooq daily close quote for a share class.

A successful response includes `asset_class`, normalized `symbol`, `price`, `currency`, `as_of`, `source`, and `is_stale`. Prices are USD-denominated. Crypto timestamps come from the provider's last-update time. Stooq supplies daily session data rather than a timezone-aware intraday timestamp; its `as_of` value marks the session date and is only a freshness approximation. For the current UTC session date, `as_of` is capped at the current time; a future session date is rejected.

## Provider and error behaviour

- `MarketDataProvider` defines the shared `get_quote(asset_class, symbol)` contract.
- `CompositeMarketDataProvider` dispatches to CoinGecko for crypto and Stooq for equities.
- Requests use fixed HTTPS provider hosts, a five-second timeout, validated symbols, finite positive prices, and provider-response validation.
- Crypto symbols are intentionally limited to BTC, ETH, and SOL. Equities are US-listed symbols supported by Stooq.
- Quotes older than two hours for crypto or four days for equities are returned with `is_stale: true`; future timestamps and malformed responses are rejected.
- Invalid asset classes/symbols return HTTP 422. Upstream failure or malformed provider data returns a sanitized HTTP 502. The endpoint does not expose provider error text.
- Public provider rate limits and availability apply. No API key or new dependency is required.

## Safety boundary

This endpoint only reads market information. It does not connect prices to policy decisions, transaction proposals, portfolio balances, broker/exchange order routing, signing, or execution. A quote is informational, can be delayed or stale, and must not be treated as a guaranteed executable price. Market data never bypasses Aegis policy, risk checks, or owner approval.
