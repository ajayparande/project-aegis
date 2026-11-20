# Upstox V3 observe-only market stream

Implemented scope: bounded LTPC cash-equity observation and offline integration.
Application mode remains `RESEARCH_ONLY`. `PAPER_RESEARCH` is disabled. No real feed
connection or authentication was performed during implementation or testing.

## Protocol references (checked 2026-11-19)

- [Official V3 market feed](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/)
- [Official V3 authorized URL](https://upstox.com/developer/api-documentation/get-market-data-feed-authorize-v3/)
- [Official instruments](https://upstox.com/developer/api-documentation/instruments/)
- [Pinned official generated protobuf source](https://github.com/upstox/upstox-python/blob/d88a12ef5738a4c8d71f3b88eb7b79a359277c9d/examples/websocket/market_data/v3/MarketDataFeedV3_pb2.py)

The adapter uses the serialized descriptor extracted from the official generated
source, decoded by `google.protobuf` in a private descriptor pool. Its SHA-256 is
`6765c281ddba1c0bec39c262e3231736f01036b76c4031bf2e4dd81fea6acf5a`.
The original Upstox permission notice accompanies the descriptor. No custom
protobuf wire parser, SDK installation or protoc installation is required.

Authorization uses only `GET https://api.upstox.com/v3/feed/market-data-feed/authorize`.
The returned single-use `wss://*.upstox.com/market-data-feeder/v3/` URL is validated
and used once. A reconnect obtains a fresh URL. HTTP redirects are not followed;
this implements the officially documented alternative to the direct redirect flow.
Subscription JSON is sent as **binary** bytes, with `method=sub`, `mode=ltpc`,
an independent request GUID and explicit instrument keys. Standard WebSocket
ping/pong is handled by the installed WebSocket library with TLS verification.

Official normal limits are two connections per user and 5000 keys for LTPC alone
(2000 per category for mixed subscriptions). This implementation uses one socket
and at most 50 keys. It cannot count sockets opened by other processes; operator
account-wide limits and entitlements remain prerequisites. `full`, `full_d30`,
`option_greeks`, indices and derivatives are deliberately rejected in this scope.

## Runtime boundary

Use `ForwardRuntimeService.create(..., observation_only=True)` with a fresh,
explicitly authorized disposable/dedicated database and unused paper account.
Existing synthetic runtime creation remains unchanged by default. Do not reuse
an existing pilot database or convert an existing execution runtime.

The persisted configuration identifies `UPSTOX_V3_OBSERVE_ONLY`. Imports use
`OBSERVED_STREAM` and `UNVERIFIED` source-use status, including offline mock feeds.
The usual `process_quote` validator and candle aggregation remain authoritative.
Observation mode creates no paper marks, scheduler opportunities, execution
authorizations, proposals, protective orders or fills. Public execution APIs
reject this runtime. Restart authenticates completed observations and F2 gaps
without generating retrospective scheduler events. Observed bars are sampled
observations, not authoritative historical exchange candles or permitted backtest
inputs. There is no switch to real-market-data paper execution in this milestone.

`UpstoxMarketStreamAdapter` requires an explicit one-to-one mapping such as
`{"NSE_EQ|INE002A01018": registered_id}`. Exchange, cash segment, active EQUITY
status, registered ISIN, runtime membership and decimal tick size are checked
again before each data frame. A display symbol alone is never a mapping.

## Numeric, time and synchronization policies

- Protobuf `LTPC.ltp` is binary64. The explicit conversion policy is
  `BINARY64_SHORTEST_REPR_V1`: `Decimal(str(decoded_double))`. This does **not**
  recover unavailable original decimal input. No float financial arithmetic or
  price rounding is performed. Invalid/nonpositive/nonfinite/off-tick prices and
  negative quantities are rejected. The original binary64 hex representation and
  frame SHA-256 are stored atomically with every accepted quote in the existing
  bounded journal health table; raw frames and authorization data are not stored.
- `ltt` is the provider last-trade event time; `currentTs` is provider emission
  time. Local receipt and the clock sampled immediately before runtime ingestion
  remain separate. Milliseconds are converted using integer timedeltas. Future
  tolerance is zero. Default observation/frame age limit is 30 seconds, capped by
  the runtime's own freshness limit. Missing provider time is never replaced by
  receipt time.
- `ltq` is sampled last-trade quantity, not a cumulative-volume delta. Aggregated
  volume is explicitly non-authoritative. LTPC has no unique trade ID: equal
  instrument/time/price/quantity observations are collapsed, including across
  restart. Distinct exchange trades with identical evidence cannot be recovered.
- Every connection starts with market-status synchronization and discards its
  initial snapshot, even when labelled `live_feed` as in official examples.
  Only subsequent LTPC updates in `NORMAL_OPEN`, without an auction indicative
  price, can reach the runtime. Unsupported or malformed frames fail closed.
- Gaps keep their actual observation timestamps. No missing candle is invented.
  Direct ingestion reports `GAPPED`; this describes uncertain historical
  continuity, not execution permission. A fresh connection resets transport
  synchronization only; it does not repair historical gaps or source-use rights.

## Transport and activation

Default mode is `OFFLINE_TEST`. Manual `connected()`/`receive_frame()` calls allow
bounded mock/replay testing without a socket. Real transport requires **all** of:
an observation-only runtime, adapter mode `OBSERVE_ONLY`, client construction with
`network_enabled=True`, and `run(..., operator_authorized=True, access_token=...)`.
No environment token is automatically loaded. Use operator-provided in-memory
secrets through the same explicit injection convention as the existing HTTP
client; do not put credentials in examples, source, SQLite or logs.

The client has a bounded frame budget and zero to three reconnects, with 1/2/4
second backoff, bounded HTTP/socket timeouts, reauthorization, resubscription,
fresh market status/snapshot synchronization, and durable duplicate detection.
Market-status traffic and ping/pong do not refresh trade observation age. Stale
or disconnected transport is recorded as health, never HOLD. Validation and
authorization failures stop immediately; transport failures exhaust a fixed retry
budget. Foreign exception details and secret redirect URLs are suppressed;
WebSocket transport logging uses an unregistered disabled logger.

The current virtual environment already provides `protobuf` and `websockets`.
No dependencies were installed. A real connection still requires separate
operator authorization, an eligible Upstox account/app/token, permitted market-data
use, verified instrument mappings and appropriate local calendar configuration.
Real handshake behavior and real feed performance have not been independently
verified. These offline tests establish neither operational readiness nor paper
execution authorization.
