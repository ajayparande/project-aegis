"""Bounded Upstox V3 LTPC observation, with no strategy or order interfaces.

Binary64 prices use Decimal(str(value)), Python's shortest round-trip rendering.
This does not restore the provider's original decimal input. The original IEEE
754 value (hex), binary frame digest and three clocks accompany each accepted
quote. LTQ is sampled last-trade quantity, never authoritative exchange volume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import socket as socket_module
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from aegis.database import get_readonly_connection
from aegis.forward_runtime import ForwardRuntimeService, ObservationEvidence, Quote, QuoteResult
from aegis.providers._upstox_v3_descriptor import DESCRIPTOR_BYTES
from aegis.providers.normalization import parse_decimal, parse_volume

MAX_FRAME_BYTES = 1_048_576
MAX_INSTRUMENTS = 50  # Conservative local cap, below the normal LTPC-only limit of 5000.
AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
_KEY = re.compile(r"(NSE|BSE)_EQ\|[A-Z]{2}[A-Z0-9]{9}[0-9]\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class UpstoxStreamError(ValueError):
    """Sanitized feed failure; provider bodies, URLs and tokens are never included."""


class StreamMode(str, Enum):
    OFFLINE_TEST = "OFFLINE_TEST"
    OBSERVE_ONLY = "OBSERVE_ONLY"
    PAPER_RESEARCH = "PAPER_RESEARCH"  # Reserved and rejected by this implementation.


class ConnectionState(str, Enum):
    OFFLINE = "OFFLINE"
    SYNCING = "SYNCING"
    LIVE = "LIVE"
    GAPPED = "GAPPED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    EXHAUSTED = "EXHAUSTED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise UpstoxStreamError("A timezone-aware clock is required.")
    return value.astimezone(UTC)


def _text(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _millis(value: object) -> datetime:
    if type(value) is not int or not 946684800000 <= value <= 4102444800000:
        raise UpstoxStreamError("Invalid provider millisecond timestamp.")
    return _EPOCH + timedelta(milliseconds=value)


def feed_message_class() -> Any:
    """Load the official descriptor with the installed protobuf runtime, in a private pool."""
    try:
        from google.protobuf import descriptor_pool, message_factory, wrappers_pb2  # type: ignore[import-untyped]
    except ImportError:
        raise UpstoxStreamError("Missing prerequisite: google.protobuf runtime.") from None
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(wrappers_pb2.DESCRIPTOR.serialized_pb)
    pool.AddSerializedFile(DESCRIPTOR_BYTES)
    return message_factory.GetMessageClass(
        pool.FindMessageTypeByName("com.upstox.marketdatafeederv3udapi.rpc.proto.FeedResponse")
    )


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """Provider evidence, independent of execution authorization."""

    instrument_key: str
    instrument_id: int
    price: Decimal
    last_trade_quantity: int
    event_at: datetime
    provider_emitted_at: datetime
    received_at: datetime
    processing_started_at: datetime
    price_binary64_hex: str
    frame_sha256: str

    @property
    def event_id(self) -> str:
        # No trade ID is supplied by LTPC. Equal evidence is collapsed; this is
        # at-most-once observation processing, not an exchange trade ledger.
        evidence = f"upstox-v3:{self.instrument_key}:{_text(self.event_at)}:{self.price_binary64_hex}:{self.last_trade_quantity}"
        return hashlib.sha256(evidence.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservationResult:
    observation: MarketObservation
    runtime_result: QuoteResult
    gap_detected: bool


class StreamStopped(Exception):
    """Expected bounded stop; only controlled reason codes cross this boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class StreamControl:
    """Per-invocation limits and small callbacks, without retaining raw frames."""

    max_seconds: int = 60
    max_observations: int = 300
    monotonic: Callable[[], float] = time.monotonic
    checkpoint: Callable[[], None] = lambda: None
    event: Callable[[str], None] = lambda code: None
    observation: Callable[[ObservationResult], None] = lambda result: None
    retain_results: bool = False
    decoded: Callable[[MarketObservation], None] = lambda observation: None
    accepted: int = field(default=0, init=False)
    stop_reason: str | None = field(default=None, init=False)
    _deadline: float = field(default=0, init=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)

    def __post_init__(self) -> None:
        if type(self.max_seconds) is not int or not 1 <= self.max_seconds <= 60:
            raise UpstoxStreamError("Session duration must be between 1 and 60 seconds.")
        if type(self.max_observations) is not int or not 1 <= self.max_observations <= 300:
            raise UpstoxStreamError("Observation limit must be between 1 and 300.")

    def start(self) -> None:
        self._deadline = self.monotonic() + self.max_seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.monotonic())

    def stop(self, reason: str = "INTERRUPTED") -> None:
        self.stop_reason = reason
        self._cancelled.set()

    def check(self) -> None:
        self.checkpoint()
        if self._cancelled.is_set():
            raise StreamStopped(self.stop_reason or "INTERRUPTED")
        if self.remaining() <= 0:
            raise StreamStopped("DURATION_LIMIT")
        if self.accepted >= self.max_observations:
            raise StreamStopped("OBSERVATION_LIMIT")

    def record(self, result: ObservationResult) -> None:
        if result.runtime_result.accepted and not result.runtime_result.duplicate:
            self.accepted += 1
        self.observation(result)
        self.checkpoint()


class UpstoxMarketStreamAdapter:
    """Validate official binary messages and ingest only into a dedicated observe-only runtime."""

    def __init__(
        self,
        runtime: ForwardRuntimeService,
        instrument_map: Mapping[str, int],
        *,
        mode: StreamMode | str = StreamMode.OFFLINE_TEST,
        subscription_mode: str = "ltpc",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_age_seconds: int = 30,
        ingest_observations: bool = True,
    ) -> None:
        try:
            self.mode = StreamMode(mode)
        except (TypeError, ValueError):
            raise UpstoxStreamError("Unknown stream operating mode.") from None
        if self.mode == StreamMode.PAPER_RESEARCH:
            raise UpstoxStreamError("PAPER_RESEARCH is reserved and disabled.")
        if subscription_mode != "ltpc":
            raise UpstoxStreamError("Only LTPC cash-equity subscriptions are supported.")
        if type(ingest_observations) is not bool:
            raise UpstoxStreamError("ingest_observations must be a boolean.")
        self.ingest_observations = ingest_observations
        if not isinstance(runtime, ForwardRuntimeService) or not runtime.observation_only:
            raise UpstoxStreamError("A dedicated observation-only runtime is required.")
        if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 60:
            raise UpstoxStreamError("Invalid bounded feed age limit.")
        if (
            not isinstance(instrument_map, Mapping)
            or not 1 <= len(instrument_map) <= MAX_INSTRUMENTS
        ):
            raise UpstoxStreamError("Invalid bounded instrument mapping.")
        self.runtime = runtime
        self.instrument_map = dict(instrument_map)
        if any(type(v) is not int or v < 1 for v in self.instrument_map.values()) or len(
            set(self.instrument_map.values())
        ) != len(self.instrument_map):
            raise UpstoxStreamError("Instrument mapping must be one-to-one.")
        self._clock = clock
        self.max_age_seconds = min(max_age_seconds, runtime.max_quote_delay_seconds)
        self.state = ConnectionState.OFFLINE
        self._market_open: dict[str, bool] = {}
        self._snapshot_pending = True
        self._last_quote_receipt: datetime | None = None
        self._connected_at: datetime | None = None
        self._last_emitted: datetime | None = None
        self._message_class = feed_message_class()
        self._instruments()

    def _instruments(self) -> dict[str, Decimal]:
        ticks = {}
        with closing(get_readonly_connection(self.runtime.database_path)) as connection:
            for key, instrument_id in self.instrument_map.items():
                match = _KEY.fullmatch(key) if isinstance(key, str) else None
                row = connection.execute(
                    "SELECT * FROM instruments WHERE id=?", (instrument_id,)
                ).fetchone()
                if match is None or row is None or instrument_id not in self.runtime.instrument_ids:
                    raise UpstoxStreamError("Unknown or unregistered instrument mapping.")
                exchange = match.group(1)
                if (
                    row["exchange"] != exchange
                    or row["segment"] not in (exchange, exchange + "_EQ")
                    or row["instrument_type"] != "EQUITY"
                    or not row["is_active"]
                    or row["isin"] != key.split("|", 1)[1]
                    or not row["tick_size_decimal"]
                ):
                    raise UpstoxStreamError(
                        "Instrument exchange, segment, ISIN or tick mapping is invalid."
                    )
                ticks[key] = parse_decimal(row["tick_size_decimal"], "tick size")
        return ticks

    def subscription_request(self) -> bytes:
        """V3 subscriptions are UTF-8 JSON carried in a binary WebSocket frame."""
        return json.dumps(
            {
                "guid": uuid4().hex,
                "method": "sub",
                "data": {
                    "mode": "ltpc",
                    "instrumentKeys": sorted(self.instrument_map),
                },
            },
            separators=(",", ":"),
        ).encode()

    @property
    def synchronized(self) -> bool:
        """Market status and an initial snapshot were validated for this connection."""
        return bool(self._market_open) and not self._snapshot_pending

    def connected(self) -> None:
        """Every connection must independently synchronize market status and a snapshot."""
        self.runtime.ensure_quote_progression_ready()
        self._connected_at = _utc(self._clock())
        self.runtime.set_observation_feed_health("STARTING", _text(self._connected_at))
        self.state = ConnectionState.SYNCING
        self._market_open.clear()
        self._snapshot_pending = True
        self._last_quote_receipt = None
        self._last_emitted = None

    def disconnected(self) -> None:
        self.runtime.set_observation_feed_health("DISCONNECTED", _text(self._clock()))
        self.state = ConnectionState.DISCONNECTED

    def poll_health(self, now: datetime | None = None) -> ConnectionState:
        """Ping/pong and market status messages do not establish fresh trade observations."""
        current = _utc(now if now is not None else self._clock())
        previous = self._last_quote_receipt or self._connected_at
        if previous is not None and current - previous > timedelta(seconds=self.max_age_seconds):
            self.runtime.set_observation_feed_health("STALE", _text(current))
            self.state = ConnectionState.STALE
        return self.state

    def receive_frame(
        self, frame: bytes, *, received_at: datetime, control: StreamControl | None = None
    ) -> tuple[ObservationResult, ...]:
        """Decode and validate an entire frame before forwarding any observation.

        Initial snapshots are synchronization only, including the first live_feed
        frame after market_info (the official examples label snapshots live_feed).
        """
        if self.state not in (
            ConnectionState.SYNCING,
            ConnectionState.LIVE,
            ConnectionState.GAPPED,
        ):
            raise UpstoxStreamError("Feed is unavailable; explicit resynchronization is required.")
        if not isinstance(frame, bytes) or not 0 < len(frame) <= MAX_FRAME_BYTES:
            raise UpstoxStreamError("Invalid binary frame or frame size.")
        received, processing = _utc(received_at), _utc(self._clock())
        if received > processing:
            raise UpstoxStreamError("Receipt timestamp cannot follow processing time.")
        message = self._message_class()
        try:
            message.ParseFromString(frame)
        except Exception:  # noqa: BLE001 -- sanitize foreign decoder errors and raw frame data
            raise UpstoxStreamError("Invalid Upstox V3 protobuf frame.") from None
        emitted = _millis(message.currentTs)
        if emitted > received or processing - emitted > timedelta(seconds=self.max_age_seconds):
            raise UpstoxStreamError("Provider frame is stale or future-dated.")
        if self._last_emitted is not None and emitted < self._last_emitted:
            raise UpstoxStreamError("Provider frame is out of order.")
        if message.type == 2:
            if not message.HasField("marketInfo") or message.feeds:
                raise UpstoxStreamError("Invalid market-status message.")
            self._market_open = {
                key: message.marketInfo.segmentStatus.get(key.split("|", 1)[0], -1) == 2
                for key in self.instrument_map
            }
            self._last_emitted = emitted
            if control:
                control.event("MARKET_STATUS_DECODED")
            return ()
        if message.type not in (0, 1) or not message.feeds or not self._market_open:
            raise UpstoxStreamError("Feed data arrived without valid market synchronization.")
        if len(message.feeds) > len(self.instrument_map):
            raise UpstoxStreamError("Feed contains unsubscribed instruments.")
        ticks, observations = self._instruments(), []
        digest = hashlib.sha256(frame).hexdigest()
        for key in sorted(message.feeds):
            feed = message.feeds[key]
            if key not in ticks or feed.WhichOneof("FeedUnion") != "ltpc" or feed.requestMode != 0:
                raise UpstoxStreamError("Unknown instrument or unsupported feed mode.")
            snapshot = message.type == 0 or self._snapshot_pending
            if (
                self.ingest_observations
                and not snapshot
                and (not self._market_open.get(key) or feed.ltpc.HasField("iep"))
            ):
                raise UpstoxStreamError("Instrument is outside normal market trading status.")
            try:
                price = parse_decimal(feed.ltpc.ltp, "price")
                quantity = parse_volume(feed.ltpc.ltq)
            except (TypeError, ValueError):
                raise UpstoxStreamError("Invalid provider price or quantity.") from None
            if price % ticks[key] != 0:
                raise UpstoxStreamError("Provider price is not aligned to the registered tick.")
            event = _millis(feed.ltpc.ltt)
            if event > emitted or (
                not self._snapshot_pending
                and message.type != 0
                and self.ingest_observations
                and processing - event > timedelta(seconds=self.max_age_seconds)
            ):
                raise UpstoxStreamError("Provider observation is stale or future-dated.")
            observations.append(
                MarketObservation(
                    key,
                    self.instrument_map[key],
                    price,
                    quantity,
                    event,
                    emitted,
                    received,
                    processing,
                    feed.ltpc.ltp.hex(),
                    digest,
                )
            )
        self._last_emitted = emitted
        if message.type == 0 or self._snapshot_pending:
            self._snapshot_pending = False
            if control:
                control.event("SNAPSHOT_DECODED")
            return ()
        if control:
            for observation in observations:
                control.event("LTPC_UPDATE_DECODED")
                control.decoded(observation)
        if not self.ingest_observations:
            return ()
        results = []
        for observation in observations:
            if control:
                if control.accepted >= control.max_observations:
                    break
                control.check()
            with closing(get_readonly_connection(self.runtime.database_path)) as connection:
                cursor = connection.execute(
                    """SELECT sequence,quote_timestamp FROM forward_quotes
                       WHERE runtime_id=? AND instrument_id=? AND accepted=1
                       ORDER BY sequence DESC LIMIT 1""",
                    (self.runtime.runtime_id, observation.instrument_id),
                ).fetchone()
            gap = cursor is not None and (
                observation.event_at.replace(second=0, microsecond=0)
                - datetime.fromisoformat(cursor["quote_timestamp"]).replace(second=0, microsecond=0)
            ) > timedelta(minutes=1)
            self.runtime.ensure_quote_progression_ready()
            ingestion_started = _utc(self._clock())
            if ingestion_started < received or ingestion_started - observation.event_at > timedelta(
                seconds=self.max_age_seconds
            ):
                raise UpstoxStreamError("Observation became stale before runtime ingestion.")
            observation = replace(observation, processing_started_at=ingestion_started)
            quote = Quote(
                observation.instrument_id,
                _text(observation.event_at),
                int(cursor["sequence"]) + 1 if cursor else 1,
                observation.price,
                observation.last_trade_quantity,
                observation.event_id,
                _text(received),
                ObservationEvidence(
                    digest,
                    observation.price_binary64_hex,
                    _text(emitted),
                    _text(ingestion_started),
                    cast(Any, self.mode.value),
                ),
            )
            outcome = self.runtime.process_quote(quote)
            results.append(ObservationResult(observation, outcome, gap))
            if control:
                control.record(results[-1])
            if outcome.accepted and not outcome.duplicate:
                self._last_quote_receipt = received
                if gap or self.state == ConnectionState.GAPPED:
                    self.state = ConnectionState.GAPPED
                else:
                    self.state = ConnectionState.LIVE
        return tuple(results)


class StreamSocket(Protocol):
    def send(self, data: bytes) -> None: ...
    def recv(self, timeout: float) -> bytes | str: ...
    def close(self) -> None: ...


def _connect_socket(uri: str, *, open_timeout: float = 5) -> StreamSocket:
    try:
        from websockets.sync.client import connect
    except ImportError:
        raise UpstoxStreamError("Missing prerequisite: websockets.sync.client.") from None
    # The authorized URI contains a single-use secret. Even global DEBUG logging
    # must not expose the handshake URL or frame payloads.
    logger = logging.Logger("aegis.upstox.private-transport")  # noqa: LOG001 -- unregistered secret-safe logger
    logger.disabled = True
    return cast(
        StreamSocket,
        connect(
            uri,
            proxy=None,
            open_timeout=open_timeout,
            close_timeout=2,
            ping_interval=20,
            ping_timeout=20,
            max_size=MAX_FRAME_BYTES,
            max_queue=16,
            logger=logger,
        ),
    )


class UpstoxMarketStreamClient:
    """Market-data authorization and bounded transport, offline unless explicitly enabled."""

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        session: requests.Session | None = None,
        connector: Callable[[str], StreamSocket] = _connect_socket,
        sleep: Callable[[float], None] = time.sleep,
        max_reconnects: int = 3,
    ) -> None:
        if (
            type(network_enabled) is not bool
            or type(max_reconnects) is not int
            or not 0 <= max_reconnects <= 3
        ):
            raise UpstoxStreamError("Invalid network enablement or reconnect bound.")
        self._enabled = network_enabled
        self._session = session if session is not None else requests.Session()
        if session is None:
            self._session.trust_env = False
        self._connector, self._sleep = connector, sleep
        self._max_reconnects = max_reconnects

    def _authorize(self, token: str, control: StreamControl | None = None) -> str:
        try:
            if control:
                control.check()
            timeout = min(5.0, max(0.001, control.remaining() / 2)) if control else 5
            response = self._session.get(
                AUTHORIZE_URL,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=(timeout, timeout),
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code != 200:
                    raise UpstoxStreamError("Market-data authorization was rejected.")
                content = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if control:
                        control.check()
                    content.extend(chunk)
                    if len(content) > 65_536:
                        raise UpstoxStreamError("Authorization response exceeds its bound.")
                body = json.loads(content)
                if body.get("status") != "success":
                    raise UpstoxStreamError("Market-data authorization was rejected.")
                uri = body["data"]["authorized_redirect_uri"]
                parsed = urlsplit(uri)
                if (
                    not isinstance(uri, str)
                    or len(uri) > 8192
                    or parsed.scheme != "wss"
                    or not parsed.hostname
                    or not parsed.hostname.endswith(".upstox.com")
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                    or parsed.port not in (None, 443)
                    or not parsed.path.startswith("/market-data-feeder/v3/")
                ):
                    raise UpstoxStreamError("Unsafe market-data authorized endpoint.")
                return uri
            finally:
                response.close()
        except StreamStopped:
            raise
        except Exception:  # noqa: BLE001 -- suppress credentials in foreign HTTP/parser errors
            if control:
                control.event("AUTHORIZATION_FAILURE")
            raise UpstoxStreamError(
                "Market-data authorization failed; provider details suppressed."
            ) from None

    def run(
        self,
        adapter: UpstoxMarketStreamAdapter,
        *,
        access_token: str,
        operator_authorized: bool = False,
        max_frames: int = 120,
        control: StreamControl | None = None,
    ) -> tuple[ObservationResult, ...]:
        """Open only the V3 market feed; no OAuth login, order API or automatic paper activation."""
        if (
            not self._enabled
            or operator_authorized is not True
            or adapter.mode != StreamMode.OBSERVE_ONLY
            or not adapter.runtime.observation_only
        ):
            raise UpstoxStreamError(
                "Real transport requires explicit observe-only operator authorization."
            )
        if type(max_frames) is not int or not 1 <= max_frames <= 10_000:
            raise UpstoxStreamError("Invalid frame-processing bound.")
        if (
            not isinstance(access_token, str)
            or not access_token
            or len(access_token) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in access_token)
        ):
            raise UpstoxStreamError("Invalid access-token input.")
        results: list[ObservationResult] = []
        frames = 0
        active: list[StreamSocket] = []
        timer = None
        if control:
            control.start()

            def deadline_stop() -> None:
                control.stop("DURATION_LIMIT")
                for connection in tuple(active):
                    # Abort a real network socket to unblock recv immediately;
                    # a polite close handshake must not extend the session.
                    raw = getattr(connection, "socket", None)
                    with suppress(Exception):
                        if isinstance(raw, socket_module.socket):
                            raw.shutdown(socket_module.SHUT_RDWR)
                            raw.close()
                        else:
                            connection.close()

            timer = threading.Timer(control.max_seconds, deadline_stop)
            timer.daemon = True
            timer.start()
        for attempt in range(self._max_reconnects + 1):
            socket: StreamSocket | None = None
            try:
                if control:
                    control.check()
                    control.event("CONNECTION_ATTEMPT")
                uri = self._authorize(
                    access_token, control
                )  # Each URL is single-use; reauthorize on reconnect.
                if control:
                    control.event("AUTHORIZATION_PASSED")
                    control.check()
                socket = (
                    _connect_socket(uri, open_timeout=min(5, control.remaining()))
                    if control and self._connector is _connect_socket
                    else self._connector(uri)
                )
                active.append(socket)
                if control:
                    control.event("CONNECTED")
                    control.check()
                adapter.connected()
                socket.send(adapter.subscription_request())
                if control:
                    control.event("SUBSCRIBED")
                synchronized = False
                while frames < max_frames:
                    if control:
                        control.check()
                    try:
                        frame = socket.recv(timeout=min(5, control.remaining()) if control else 5)
                    except TimeoutError:
                        if control:
                            control.check()
                            if adapter.poll_health() != ConnectionState.STALE:
                                continue
                            control.event("STALE")
                        raise
                    if control:
                        control.check()
                    received = _utc(adapter._clock())
                    if adapter.poll_health(received) == ConnectionState.STALE:
                        if control:
                            control.event("STALE")
                        raise TimeoutError("Feed requires resynchronization.")
                    frames += 1
                    if not isinstance(frame, bytes):
                        raise UpstoxStreamError("Expected a binary provider frame.")
                    outcomes = adapter.receive_frame(frame, received_at=received, control=control)
                    if not control or control.retain_results:
                        results.extend(outcomes)
                    if control and adapter.synchronized and not synchronized:
                        control.event("SYNCHRONIZED")
                        synchronized = True
                adapter.disconnected()
                adapter.state = ConnectionState.CLOSED
                if control:
                    control.stop_reason = "FRAME_LIMIT"
                if timer:
                    timer.cancel()
                return tuple(results)
            except StreamStopped as exc:
                if control:
                    control.stop_reason = exc.reason
                adapter.disconnected()
                adapter.state = ConnectionState.CLOSED
                if timer:
                    timer.cancel()
                return tuple(results)
            except UpstoxStreamError:
                if control:
                    control.event("VALIDATION_OR_AUTHORIZATION_FAILURE")
                adapter.disconnected()
                adapter.state = ConnectionState.FAILED
                if timer:
                    timer.cancel()
                raise UpstoxStreamError(
                    "Market-data stream failed validation or authorization."
                ) from None
            except Exception:  # noqa: BLE001 -- foreign transport errors may contain secret URLs
                if control:
                    try:
                        control.check()
                    except StreamStopped as exc:
                        control.stop_reason = exc.reason
                        if timer:
                            timer.cancel()
                        adapter.disconnected()
                        adapter.state = ConnectionState.CLOSED
                        return tuple(results)
                adapter.poll_health()
                adapter.disconnected()
                if attempt == self._max_reconnects:
                    if timer:
                        timer.cancel()
                    adapter.state = ConnectionState.EXHAUSTED
                    raise UpstoxStreamError("Market-data reconnect limit exhausted.") from None
                if control:
                    control.event("RECONNECT")
                self._sleep(
                    min(float(2**attempt), control.remaining()) if control else float(2**attempt)
                )
            except BaseException:
                if timer:
                    timer.cancel()
                adapter.disconnected()
                raise
            finally:
                if socket is not None:
                    active.clear()
                    with suppress(Exception):
                        socket.close()
                if control:
                    control.event("DISCONNECTED")
        raise UpstoxStreamError("Market-data stream stopped.")
