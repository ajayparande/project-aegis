"""Isolated synthetic M5-B1 risk, evidence and no-side-effect regressions."""

import hashlib
import json
import socket
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from aegis.backtesting import _fee_total, _fees, default_cost_assumptions
from aegis.database import get_connection, get_readonly_connection, initialize_database
from aegis.forward_runtime import ForwardRuntimeService, Quote
from aegis.instruments import InstrumentInput, register_instrument
from aegis.multi_instrument_runtime import _MultiInstrumentCoordinator
from aegis.paper_trading import RiskLimits, create_paper_account
from aegis.portfolio import CapitalAllocation, PortfolioPolicy
from aegis.portfolio_pretrade import (
    _UNIVERSE,
    _equal_budgets,
    _evaluate_synthetic_portfolio,
    _EvidenceReference,
    _InstrumentPretradeIntent,
    _policy_id,
    _PortfolioPretradeCoordinator,
    _PortfolioPretradeRequest,
    _PretradeError,
)
from tests.test_forward_runtime import _START, _calendar

_SYMBOLS = ("RELIANCE", "TCS", "INFY")
_RISK = RiskLimits(Decimal(5000), 100, Decimal(5000), Decimal(10000), Decimal(500), 60)


def _build(
    root,
    monkeypatch,
    *,
    risk=_RISK,
    costs=None,
    cash=Decimal(10000),
    gap=False,
    restart=False,
    reverse=False,
):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AEGIS_DATABASE_PATH", str(root / "unused.db"))
    monkeypatch.setenv("AEGIS_LOG_FILE", str(root / "unused.log"))
    monkeypatch.setenv("AEGIS_MODE", "RESEARCH_ONLY")
    monkeypatch.setattr(
        socket.socket, "connect", lambda *a: pytest.fail("No broker/network connections")
    )
    db = root / "synthetic.db"
    initialize_database(db)
    calendars = _calendar(root / "calendar.json")
    monkeypatch.setenv("AEGIS_CALENDAR_CONFIG_PATH", str(root / "calendar.json"))
    ids = tuple(
        register_instrument(
            db,
            InstrumentInput(
                "NSE",
                symbol,
                symbol + " SYNTHETIC",
                "EQUITY",
                segment="NSE",
                tick_size=Decimal("0.05"),
            ),
        ).id
        for symbol in _SYMBOLS
    )
    costs = default_cost_assumptions() if costs is None else costs
    account = create_paper_account(
        db,
        name="pretrade-synthetic",
        initial_cash=cash,
        risk_limits=risk,
        costs=costs,
        created_at=_START.isoformat(),
    )
    runtime = ForwardRuntimeService.create(
        db,
        "pretrade-synthetic",
        account,
        ids,
        calendars,
        scheduling_only=True,
        created_at=_START.isoformat(),
    )
    coordinator = _MultiInstrumentCoordinator(runtime, _SYMBOLS)
    sequences = dict.fromkeys(ids, 0)
    for minute in range(3):
        for instrument in tuple(reversed(ids)) if reverse else ids:
            if gap and instrument == ids[2] and minute == 1:
                continue
            sequences[instrument] += 1
            at = (_START + timedelta(minutes=minute)).isoformat()
            quote = Quote(
                instrument,
                at,
                sequences[instrument],
                Decimal(100 * instrument),
                10,
                f"synthetic-{instrument}-{minute}",
                at,
            )
            assert coordinator.process((quote,))[0].accepted
            if restart:
                coordinator = _MultiInstrumentCoordinator.open(
                    db, runtime.runtime_id, calendars, _SYMBOLS
                )
    intents = []
    with closing(get_readonly_connection(db)) as conn:
        for identity in _UNIVERSE:
            instrument = ids[_SYMBOLS.index(identity.split(":")[1])]
            q = conn.execute(
                "SELECT event_id FROM forward_quotes WHERE instrument_id=? ORDER BY sequence DESC LIMIT 1",
                (instrument,),
            ).fetchone()
            candle = conn.execute(
                "SELECT candle_timestamp FROM forward_runtime_candles WHERE instrument_id=? AND interval='1m' ORDER BY candle_timestamp DESC LIMIT 1",
                (instrument,),
            ).fetchone()
            ref = _EvidenceReference(runtime.runtime_id, instrument, q[0], candle[0])
            action = "HOLD" if identity == "NSE:TCS" else "BUY"
            intents.append(
                _InstrumentPretradeIntent(identity, action, 0 if action == "HOLD" else 10, ref)
            )
    request = _PortfolioPretradeRequest(
        "bounded-evaluation",
        _START + timedelta(minutes=2),
        account,
        runtime.runtime_id,
        _UNIVERSE,
        cash,
        _equal_budgets(cash),
        tuple(intents),
        risk,
        costs,
        _policy_id(risk, costs),
    )
    return db, runtime, calendars, request


@pytest.fixture
def sample(tmp_path, monkeypatch):
    return _build(tmp_path, monkeypatch)


def _dump(db):
    with closing(get_readonly_connection(db)) as conn:
        return "\n".join(conn.iterdump())


def _evaluate(sample):
    db, _, _, request = sample
    return _evaluate_synthetic_portfolio(db, request)


def _changed_intent(request, **changes):
    first, *others = request.intents
    return replace(request, intents=(replace(first, **changes), *others))


def test_three_sleeves_costs_cash_and_immutable_report(sample):
    db, _, _, request = sample
    before = _dump(db)
    result = _evaluate(sample)
    assert (result.accepted_count, result.blocked_count, result.hold_count) == (2, 0, 1)
    assert tuple(d.reserved_hypothetical_cash for d in result.decisions) == (
        Decimal("3006.63"),
        Decimal("1002.54"),
        Decimal(0),
    )
    assert result.total_reserved_cash == Decimal("4009.17")
    assert result.total_buy_costs == Decimal("6.17")
    assert result.remaining_unreserved_cash == Decimal("5990.83")
    assert tuple(d.instrument for d in result.decisions) == _UNIVERSE
    assert tuple(d.sleeve_budget for d in result.decisions) == (
        Decimal("3333.34"),
        Decimal("3333.33"),
        Decimal("3333.33"),
    )
    CapitalAllocation(request.available_cash, request.sleeve_budgets)
    assert PortfolioPolicy().unused_cash_policy == "IDLE_IN_SLEEVE"
    assert result.total_sleeve_budgets == request.available_cash
    assert result.total_reserved_cash == result.total_buy_notional + result.total_buy_costs
    assert result.remaining_unreserved_cash + result.total_reserved_cash == request.available_cash
    assert result.total_hypothetical_exposure <= request.risk_limits.max_portfolio_exposure
    assert result.total_buy_costs > 0
    for d in result.decisions:
        assert type(d.approved_hypothetical_quantity) is int
        assert d.reserved_hypothetical_cash <= d.sleeve_budget
        assert d.remaining_hypothetical_cash >= 0
        assert len(d.evidence_fingerprint) == 64
        if d.requested_action == "BUY":
            assert d.estimated_transaction_costs == _fee_total(
                _fees(d.estimated_price, 10, "BUY", request.costs)
            )
        else:
            assert d.approved_hypothetical_quantity == d.reserved_hypothetical_cash == 0
    with pytest.raises(FrozenInstanceError):
        result.decisions[0].approved_hypothetical_quantity = 999
    with pytest.raises(FrozenInstanceError):
        result.decisions = ()
    assert _dump(db) == before
    with closing(get_readonly_connection(db)) as conn:
        for table in (
            "paper_orders",
            "paper_fills",
            "forward_execution_authorizations",
            "paper_execution_snapshot_uses",
            "decision_journal_events",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM forward_scheduler_events WHERE status != 'PENDING' OR proposal_binding_state != 'UNBOUND'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "cash,expected",
    [("10000", ("3333.34", "3333.33", "3333.33")), ("10000.01", ("3333.34", "3333.34", "3333.33"))],
)
def test_m2_remainders(cash, expected):
    assert tuple(s.cash for s in _equal_budgets(Decimal(cash))) == tuple(map(Decimal, expected))


@pytest.mark.parametrize(
    "value", [10000.0, True, Decimal("NaN"), Decimal("Infinity"), Decimal("10000.001"), Decimal(-1)]
)
def test_reject_invalid_money(sample, value):
    with pytest.raises(ValueError):
        replace(sample[3], available_cash=value)


@pytest.mark.parametrize("quantity", [True, False, 1.0, Decimal(1), -1, 0, 2**63])
def test_reject_invalid_quantity(sample, quantity):
    with pytest.raises(ValueError):
        _changed_intent(sample[3], requested_quantity=quantity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", "SHORT"),
        ("instrument", ""),
        ("instrument", "NSE:FOREIGN"),
        ("evidence", {"eligible": True, "price": "1"}),
    ],
)
def test_reject_unvalidated_intent(sample, field, value):
    with pytest.raises(ValueError):
        _changed_intent(sample[3], **{field: value})


def test_reject_duplicate_and_invalid_timestamp(sample):
    r = sample[3]
    with pytest.raises(ValueError, match="duplicate"):
        replace(r, intents=(r.intents[0], r.intents[0], r.intents[2]))
    with pytest.raises(ValueError, match="timezone"):
        replace(r, evaluated_at=r.evaluated_at.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(r.intents[0].evidence, instrument_id=True)
    with pytest.raises(ValueError):
        replace(r.intents[0].evidence, candle_timestamp="not-a-timestamp")


def test_canonical_reordering_duplicate_requests_and_decimal_context(sample):
    db, _, _, r = sample
    before = _dump(db)
    result = _evaluate(sample)
    reordered = replace(
        r,
        universe=tuple(reversed(r.universe)),
        intents=tuple(reversed(r.intents)),
        sleeve_budgets=tuple(reversed(r.sleeve_budgets)),
    )
    for _ in range(2):
        assert _evaluate_synthetic_portfolio(db, reordered) == result
    with localcontext() as ctx:
        ctx.prec = 6
        assert (
            _PortfolioPretradeCoordinator(db).evaluate(r).canonical_json()
            == result.canonical_json()
        )
    assert _dump(db) == before


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("max_order_value", Decimal(500), "MAX_ORDER_VALUE"),
        ("max_position_quantity", 5, "MAX_POSITION_QUANTITY"),
        ("max_instrument_exposure", Decimal(500), "MAX_INSTRUMENT_EXPOSURE"),
        ("max_portfolio_exposure", Decimal(3500), "MAX_PORTFOLIO_EXPOSURE"),
        ("max_daily_loss", Decimal("0.01"), "MAX_DAILY_LOSS"),
    ],
)
def test_phase8_limits(tmp_path, monkeypatch, field, value, code):
    sample = _build(tmp_path, monkeypatch, risk=replace(_RISK, **{field: value}))
    before = _dump(sample[0])
    result = _evaluate(sample)
    assert any(code in d.reason_codes for d in result.decisions)
    assert all(
        d.approved_hypothetical_quantity == 0 for d in result.decisions if code in d.reason_codes
    )
    assert result.total_reserved_cash <= sample[3].available_cash
    assert _dump(sample[0]) == before


def test_maximum_affordable_includes_costs_and_limits(sample):
    db, _, _, r = sample
    auto = _changed_intent(r, requested_quantity=None)
    d = _evaluate_synthetic_portfolio(db, auto).decisions[0]
    assert d.risk_decision == "APPROVED_HYPOTHETICAL"
    q, price = d.approved_hypothetical_quantity, d.estimated_price
    assert q == 11 and price > Decimal(300)
    assert price * q + _fee_total(_fees(price, q, "BUY", r.costs)) <= d.sleeve_budget
    assert price * (q + 1) + _fee_total(_fees(price, q + 1, "BUY", r.costs)) > d.sleeve_budget
    oversized = _changed_intent(r, requested_quantity=100)
    blocked = _evaluate_synthetic_portfolio(db, oversized)
    assert blocked.decisions[0].reason_codes == ("INSUFFICIENT_SLEEVE_CASH",)
    assert blocked.decisions[1].risk_decision == "APPROVED_HYPOTHETICAL"
    assert blocked.decisions[0].remaining_hypothetical_cash == r.sleeve_budgets[0].cash


def test_auto_sizing_obeys_position_limit(tmp_path, monkeypatch):
    db, _, _, r = _build(tmp_path, monkeypatch, risk=replace(_RISK, max_position_quantity=2))
    d = _evaluate_synthetic_portfolio(db, _changed_intent(r, requested_quantity=None)).decisions[0]
    assert d.risk_decision == "APPROVED_HYPOTHETICAL" and d.approved_hypothetical_quantity == 2


def test_fees_make_nominally_affordable_quantity_unaffordable(tmp_path, monkeypatch):
    costly = replace(
        default_cost_assumptions(),
        brokerage_rate=Decimal("0.5"),
        brokerage_cap_per_order=Decimal(0),
    )
    db, _, _, r = _build(tmp_path, monkeypatch, costs=costly)
    d = _evaluate_synthetic_portfolio(db, r).decisions[0]
    assert d.estimated_notional < d.sleeve_budget
    assert d.estimated_notional + d.estimated_transaction_costs > d.sleeve_budget
    assert d.reason_codes == ("INSUFFICIENT_SLEEVE_CASH",)
    assert d.reserved_hypothetical_cash == 0


@pytest.mark.parametrize("value", [True, 0.1])
def test_cost_and_risk_money_reject_float_or_bool(sample, value):
    with pytest.raises(ValueError):
        replace(sample[3].costs, brokerage_rate=value)
    with pytest.raises(ValueError):
        replace(sample[3].risk_limits, max_order_value=value)


def test_sell_exceeding_trusted_zero_holding(sample):
    db, _, _, r = sample
    d = _evaluate_synthetic_portfolio(
        db, _changed_intent(r, action="SELL", requested_quantity=1)
    ).decisions[0]
    assert d.reason_codes == ("SELL_EXCEEDS_TRUSTED_HOLDING",)
    assert d.approved_hypothetical_quantity == d.reserved_hypothetical_cash == 0


@pytest.mark.parametrize("delta,reason", [(61, "STALE_EVIDENCE"), (-1, "FUTURE_EVIDENCE")])
def test_stale_and_future(sample, delta, reason):
    db, _, _, r = sample
    result = _evaluate_synthetic_portfolio(
        db, replace(r, evaluated_at=r.evaluated_at + timedelta(seconds=delta))
    )
    assert all(d.reason_codes == (reason,) for d in result.decisions)
    assert result.total_reserved_cash == 0


@pytest.mark.parametrize("kind", ["missing", "instrument", "session", "quote", "candle"])
def test_reference_authentication_and_unaffected_peers(sample, kind):
    db, _, _, r = sample
    ref = r.intents[0].evidence
    replacement = (
        None
        if kind == "missing"
        else replace(
            ref,
            **{
                "instrument": {"instrument_id": 1},
                "session": {"runtime_id": "foreign"},
                "quote": {"quote_event_id": "foreign"},
                "candle": {"candle_timestamp": _START.isoformat()},
            }[kind],
        )
    )
    result = _evaluate_synthetic_portfolio(db, _changed_intent(r, evidence=replacement))
    assert result.decisions[0].risk_decision == "UNAVAILABLE_EVIDENCE"
    assert result.decisions[1].risk_decision == "APPROVED_HYPOTHETICAL"


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM forward_runtime_candles WHERE instrument_id=3 AND interval='1m'",
        "UPDATE data_imports SET source_use_status='UNVERIFIED' WHERE instrument_id=3",
        "UPDATE data_imports SET import_type='CSV' WHERE instrument_id=3",
        "UPDATE data_imports SET metadata_json='{}' WHERE instrument_id=3",
        "UPDATE historical_candles SET close_decimal='999' WHERE instrument_id=3",
        "UPDATE forward_quotes SET price_decimal='301' WHERE instrument_id=3 AND sequence=2",
        "UPDATE forward_quotes SET price_decimal='301' WHERE instrument_id=3 AND sequence=3",
    ],
)
def test_changed_or_missing_persisted_evidence_invalidates_approval(sample, mutation):
    db, _, _, r = sample
    prior = _evaluate(sample)
    assert prior.decisions[0].risk_decision == "APPROVED_HYPOTHETICAL"
    with closing(get_connection(db)) as conn:
        conn.execute(mutation)
        conn.commit()
    before = _dump(db)
    result = _evaluate_synthetic_portfolio(db, r)
    assert result.decisions[0].risk_decision == "UNAVAILABLE_EVIDENCE"
    assert result.fingerprint != prior.fingerprint
    assert result.decisions[1] == prior.decisions[1]
    assert _dump(db) == before


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("UPDATE paper_accounts SET halted=1", "ACCOUNT_OR_RUNTIME_HALTED"),
        ("UPDATE paper_accounts SET initial_cash='9000'", "AVAILABLE_CASH_MISMATCH"),
        ("UPDATE paper_accounts SET risk_limits_json='{}'", "PERSISTED_RISK_COST_MISMATCH"),
        ("UPDATE forward_runtimes SET state_sha256='forged'", "RUNTIME_CHECKSUM_MISMATCH"),
        (
            "INSERT INTO paper_positions(account_id,instrument_id,quantity,average_entry_price,remaining_entry_fees,realized_net_pnl) VALUES(1,1,1,'100','0','0')",
            "ACCOUNT_RECONCILIATION_FAILED",
        ),
    ],
)
def test_persisted_account_and_risk_gate(sample, mutation, code):
    db = sample[0]
    with closing(get_connection(db)) as conn:
        conn.execute(mutation)
        conn.commit()
    before = _dump(db)
    result = _evaluate(sample)
    assert result.total_reserved_cash == 0
    assert all(d.reason_codes == (code,) for d in result.decisions)
    assert _dump(db) == before


def test_caller_cannot_weaken_persisted_risk(sample):
    db, _, _, r = sample
    risk = replace(r.risk_limits, max_position_quantity=999)
    forged = replace(r, risk_limits=risk, risk_policy_id=_policy_id(risk, r.costs))
    assert all(
        d.reason_codes == ("PERSISTED_RISK_COST_MISMATCH",)
        for d in _evaluate_synthetic_portfolio(db, forged).decisions
    )
    with pytest.raises(_PretradeError):
        _PortfolioPretradeCoordinator(db).evaluate({"eligible": True})


def test_new_observation_revalidates_instead_of_caching_approval(sample):
    db, runtime, _, r = sample
    prior = _evaluate(sample)
    at = (_START + timedelta(minutes=3)).isoformat()
    assert runtime.process_quote(Quote(3, at, 4, Decimal(500), 10, "synthetic-new", at)).accepted
    before = _dump(db)
    old = _evaluate_synthetic_portfolio(db, r)
    assert old.decisions[0].reason_codes == ("EVIDENCE_SUPERSEDED",)
    # References must use the actual canonical persisted candle identity.
    with closing(get_readonly_connection(db)) as conn:
        candle = conn.execute(
            "SELECT candle_timestamp FROM forward_runtime_candles WHERE instrument_id=3 AND interval='1m' ORDER BY candle_timestamp DESC LIMIT 1"
        ).fetchone()[0]
    updated = _changed_intent(
        replace(r, evaluated_at=_START + timedelta(minutes=3)),
        evidence=replace(
            r.intents[0].evidence, quote_event_id="synthetic-new", candle_timestamp=candle
        ),
    )
    current = _evaluate_synthetic_portfolio(db, updated)
    assert current.decisions[0].reason_codes == ("INSUFFICIENT_SLEEVE_CASH",)
    assert current.fingerprint != prior.fingerprint
    assert _dump(db) == before


def test_instrument_gap_does_not_approve_missing_coverage(tmp_path, monkeypatch):
    sample = _build(tmp_path, monkeypatch, gap=True)
    result = _evaluate(sample)
    assert result.decisions[0].risk_decision == "UNAVAILABLE_EVIDENCE"
    assert result.decisions[0].reason_codes == ("MISSING_OBSERVATION_INTERVAL",)
    assert result.decisions[1].risk_decision == "APPROVED_HYPOTHETICAL"


def test_calendar_not_promoted(sample):
    db, _, calendars, _ = sample
    path = calendars.configuration_path
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["calendars"][0]["confidence"] = "CONFIGURED"
    path.write_text(json.dumps(doc), encoding="utf-8")
    before = _dump(db)
    assert all(
        d.reason_codes == ("CALENDAR_UNAVAILABLE_OR_UNCONFIRMED",)
        for d in _evaluate(sample).decisions
    )
    assert _dump(db) == before


def test_calendar_change_during_snapshot_fails_closed(sample, monkeypatch):
    import aegis.portfolio_pretrade as module
    from aegis.market_quality import CalendarConfigError

    real = module.load_approved_calendar_registry

    def changed(*args, **kwargs):
        if kwargs.get("expected_registry") is not None:
            raise CalendarConfigError("changed")
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "load_approved_calendar_registry", changed)
    before = _dump(sample[0])
    result = _evaluate(sample)
    assert result.accepted_count == 0 and result.total_reserved_cash == 0
    assert all(d.reason_codes == ("CALENDAR_CHANGED_DURING_EVALUATION",) for d in result.decisions)
    assert _dump(sample[0]) == before


def test_no_execution_or_mutating_reconciliation_api_used(sample, monkeypatch):
    import aegis.forward_paper_trading as forward
    import aegis.paper_trading as paper

    def forbidden(*args, **kwargs):
        pytest.fail("Pretrade attempted an execution or mutating API")

    monkeypatch.setattr(paper, "submit_paper_order", forbidden)
    monkeypatch.setattr(paper, "reconcile_paper_account", forbidden)
    monkeypatch.setattr(forward.ForwardPaperTradingEngine, "route_proposal", forbidden)
    for name in (
        "open",
        "prepare_evidence",
        "build_mock_proposal",
        "build_sma_proposal",
        "route_event",
        "_monitor_position",
    ):
        monkeypatch.setattr(ForwardRuntimeService, name, forbidden)
    assert _evaluate(sample).accepted_count == 2


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("intent", "requested_quantity", Decimal("1.5")),
        ("intent", "requested_quantity", 1.5),
        ("intent", "requested_quantity", True),
        ("intent", "requested_quantity", -1),
        ("intent", "action", "UNKNOWN"),
        ("intent", "action", ["BUY"]),
        ("intent", "instrument", "NSE:FOREIGN"),
        ("intent", "instrument", "NSE:RELIANCE"),
        ("intent", "evidence", {"eligible": True}),
        ("evidence", "instrument_id", True),
        ("evidence", "quote_event_id", []),
        ("evidence", "candle_timestamp", "invalid"),
        ("request", "evaluated_at", "invalid"),
        ("request", "intents", []),
        ("request", "universe", ["NSE:INFY", "NSE:RELIANCE", "NSE:TCS"]),
        ("request", "sleeve_budgets", []),
        ("risk", "max_position_quantity", 0),
        ("costs", "brokerage_rate", 0.1),
        ("sleeve", "cash", 3333.34),
    ],
)
def test_altered_request_tree_rejected_before_database_reads(
    sample, monkeypatch, target, field, value
):
    import aegis.portfolio_pretrade as module

    db, _, _, r = sample
    before = _dump(db)
    targets = {
        "request": r,
        "intent": r.intents[0],
        "evidence": r.intents[0].evidence,
        "risk": r.risk_limits,
        "costs": r.costs,
        "sleeve": r.sleeve_budgets[0],
    }
    object.__setattr__(targets[target], field, value)

    def forbidden(*args, **kwargs):
        pytest.fail("Malformed nested request reached the database")

    monkeypatch.setattr(module, "get_readonly_connection", forbidden)
    monkeypatch.setattr(module, "_equal_budgets", forbidden)
    with pytest.raises(ValueError):
        _PortfolioPretradeCoordinator(db).evaluate(r)
    assert _dump(db) == before


def test_evaluation_detaches_caller_owned_nested_values(sample, monkeypatch):
    import aegis.portfolio_pretrade as module

    db, _, _, r = sample
    expected = _evaluate(sample)
    real_open = module.get_readonly_connection

    def mutate_after_validation(path):
        object.__setattr__(r.intents[0], "requested_quantity", Decimal("1.5"))
        object.__setattr__(r.intents[0].evidence, "quote_event_id", "foreign")
        return real_open(path)

    before = _dump(db)
    monkeypatch.setattr(module, "get_readonly_connection", mutate_after_validation)
    assert _PortfolioPretradeCoordinator(db).evaluate(r) == expected
    assert _dump(db) == before


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("decision", "approved_hypothetical_quantity", Decimal("1.5")),
        ("decision", "approved_hypothetical_quantity", True),
        ("decision", "requested_action", "UNKNOWN"),
        ("decision", "requested_action", ["BUY"]),
        ("decision", "risk_decision", "EXECUTABLE"),
        ("decision", "reason_codes", ["BOUNDED_SYNTHETIC_PRETRADE_ONLY"]),
        ("decision", "estimated_price", 300.2),
        ("decision", "estimated_notional", Decimal("NaN")),
        ("decision", "reserved_hypothetical_cash", Decimal(-1)),
        ("decision", "remaining_hypothetical_cash", Decimal(0)),
        ("decision", "evidence_fingerprint", "forged"),
        ("result", "decisions", []),
        ("result", "accepted_count", True),
        ("result", "total_buy_costs", Decimal(0)),
        ("result", "remaining_unreserved_cash", Decimal(-1)),
        ("result", "request_fingerprint", "0" * 64),
        ("result", "evaluation_id", "foreign"),
        ("result", "evaluated_at", _START.replace(tzinfo=None)),
    ],
)
def test_malformed_producer_result_cannot_escape(sample, monkeypatch, target, field, value):
    db, _, _, r = sample
    result = _evaluate(sample)
    object.__setattr__(result.decisions[0] if target == "decision" else result, field, value)
    monkeypatch.setattr(_PortfolioPretradeCoordinator, "_evaluate_snapshot", lambda *args: result)
    before = _dump(db)
    with pytest.raises(ValueError):
        _PortfolioPretradeCoordinator(db).evaluate(r)
    assert _dump(db) == before


def test_inconsistent_canonical_result_fingerprint_is_rejected(sample, monkeypatch):
    from aegis.portfolio_pretrade import _PortfolioPretradeResult

    monkeypatch.setattr(_PortfolioPretradeResult, "canonical_json", lambda self: "{}")
    before = _dump(sample[0])
    with pytest.raises(ValueError, match="fingerprint"):
        _evaluate(sample)
    assert _dump(sample[0]) == before


@pytest.mark.parametrize("matching_request", [False, True])
def test_funding_tampering_cannot_be_authenticated_by_matching_cash(sample, matching_request):
    db, _, _, r = sample
    with closing(get_connection(db)) as conn:
        conn.execute("UPDATE paper_accounts SET initial_cash='15000'")
        conn.commit()
    if matching_request:
        r = replace(r, available_cash=Decimal(15000), sleeve_budgets=_equal_budgets(Decimal(15000)))
    before = _dump(db)
    result = _PortfolioPretradeCoordinator(db).evaluate(r)
    assert result.accepted_count == 0 and result.total_reserved_cash == 0
    if matching_request:
        assert all(
            d.risk_decision == "BLOCKED" and d.reason_codes == ("ACCOUNT_INCEPTION_INTEGRITY",)
            for d in result.decisions
        )
    else:
        assert all(d.reason_codes == ("AVAILABLE_CASH_MISMATCH",) for d in result.decisions)
    assert _dump(db) == before


def test_funding_request_without_matching_budgets_is_invalid(sample):
    db, _, _, r = sample
    object.__setattr__(r, "available_cash", Decimal(15000))
    before = _dump(db)
    with pytest.raises(ValueError, match="Budgets"):
        _PortfolioPretradeCoordinator(db).evaluate(r)
    assert _dump(db) == before


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_origin",
        "missing_runtime",
        "origin_hash",
        "runtime_hash",
        "wrong_owner",
        "sequence_gap",
        "contradictory_cash",
        "future_checkpoint",
    ],
)
def test_inception_and_equity_tampering_fail_closed(sample, scenario):
    from aegis.paper_equity import record_equity_checkpoint

    db, runtime, _, r = sample
    if scenario in ("sequence_gap", "contradictory_cash", "future_checkpoint"):
        at = r.evaluated_at + timedelta(seconds=1 if scenario == "future_checkpoint" else 0)
        assert record_equity_checkpoint(
            db, r.account_id, session=r.runtime_id, as_of=at.isoformat()
        )
    with closing(get_connection(db)) as conn:
        # Corruption simulation is confined to this test's disposable database.
        if scenario.startswith("missing") or scenario == "sequence_gap":
            conn.execute("DROP TRIGGER equity_no_delete")
            session = (
                f"paper-account:{r.account_id}" if scenario == "missing_origin" else r.runtime_id
            )
            conn.execute(
                "DELETE FROM paper_equity_snapshots WHERE session_id=? AND sequence=1", (session,)
            )
        elif scenario.endswith("hash"):
            conn.execute("DROP TRIGGER equity_no_update")
            session = f"paper-account:{r.account_id}" if scenario == "origin_hash" else r.runtime_id
            conn.execute(
                "UPDATE paper_equity_snapshots SET payload_sha256=? WHERE session_id=?",
                ("0" * 64, session),
            )
        elif scenario == "contradictory_cash":
            conn.execute("DROP TRIGGER equity_no_update")
            row = conn.execute(
                "SELECT snapshot_id,payload_json FROM paper_equity_snapshots WHERE session_id=? AND sequence=2",
                (r.runtime_id,),
            ).fetchone()
            document = json.loads(row["payload_json"])
            document["cash"] = "9999"
            encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "UPDATE paper_equity_snapshots SET payload_json=?,payload_sha256=? WHERE snapshot_id=?",
                (encoded, hashlib.sha256(encoded.encode()).hexdigest(), row["snapshot_id"]),
            )
        elif scenario == "wrong_owner":
            conn.execute(
                """INSERT INTO forward_runtimes(runtime_id,account_id,config_json,config_sha256,state_json,state_sha256,status,created_at,updated_at)
                SELECT 'foreign-owner',account_id,config_json,config_sha256,state_json,state_sha256,status,created_at,updated_at
                FROM forward_runtimes WHERE runtime_id=?""",
                (runtime.runtime_id,),
            )
        conn.commit()
    before = _dump(db)
    result = _evaluate(sample)
    assert all(
        d.risk_decision == "BLOCKED" and d.reason_codes == ("ACCOUNT_INCEPTION_INTEGRITY",)
        for d in result.decisions
    )
    assert result.total_reserved_cash == 0
    assert _dump(db) == before


def test_wrong_account_identity_and_single_read_transaction(sample, monkeypatch):
    import aegis.portfolio_pretrade as module

    db, _, _, r = sample
    before = _dump(db)
    assert (
        _PortfolioPretradeCoordinator(db).evaluate(replace(r, account_id=999)).accepted_count == 0
    )
    real_open, real_verify = module.get_readonly_connection, module.verify_account_inception
    opened = []

    def open_once(path):
        conn = real_open(path)
        opened.append(conn)
        return conn

    def verify_same_snapshot(conn, account, session):
        assert conn is opened[0] and conn.in_transaction
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        return real_verify(conn, account, session)

    monkeypatch.setattr(module, "get_readonly_connection", open_once)
    monkeypatch.setattr(module, "verify_account_inception", verify_same_snapshot)
    result = _evaluate(sample)
    assert result.accepted_count == 2 and len(opened) == 1
    assert _dump(db) == before


@pytest.mark.parametrize("activity", ["order", "fill", "position", "cash_movement"])
def test_existing_activity_cannot_be_hidden_by_flat_account_claim(sample, activity):
    db, _, _, r = sample
    at = r.evaluated_at.isoformat()
    with closing(get_connection(db)) as conn:
        if activity == "position":
            conn.execute(
                """INSERT INTO paper_positions(account_id,instrument_id,quantity,
                average_entry_price,remaining_entry_fees,realized_net_pnl)
                VALUES(?,1,1,'100','0','0')""",
                (r.account_id,),
            )
        else:
            # Seed pre-existing synthetic evidence, never invoke an execution API.
            conn.execute(
                """INSERT INTO paper_orders(order_id,account_id,idempotency_key,
                request_sha256,requested_instrument_id,side,quantity,status,
                signal_timestamp,submitted_timestamp,execution_timestamp,reference_price,created_at)
                VALUES('prior-order',?,'prior-key',?,1,'BUY',1,'REJECTED',?,?,?,'100',?)""",
                (r.account_id, "0" * 64, at, at, at, at),
            )
            for seq, status in enumerate(("SUBMITTED", "REJECTED"), 1):
                conn.execute(
                    "INSERT INTO paper_order_events VALUES(?, 'prior-order', ?, ?, ?, '{}')",
                    (f"prior-{seq}", seq, status, at),
                )
            if activity in ("fill", "cash_movement"):
                conn.execute(
                    """INSERT INTO paper_fills VALUES(
                    'prior-fill','prior-order',1,1,'100','{}','{}',?)""",
                    (at,),
                )
            if activity == "cash_movement":
                conn.execute(
                    """INSERT INTO paper_ledger_entries VALUES(
                    'prior-debit',?,'prior-order','prior-fill','BUY_CASH_DEBIT','-100',?)""",
                    (r.account_id, at),
                )
        conn.commit()
    before = _dump(db)
    result = _evaluate(sample)
    assert result.accepted_count == 0 and result.total_reserved_cash == 0
    assert _dump(db) == before


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("cash", Decimal(1), "INSUFFICIENT_SLEEVE_CASH"),
        ("max_order_value", Decimal(1), "MAX_ORDER_VALUE"),
        ("max_instrument_exposure", Decimal(1), "MAX_INSTRUMENT_EXPOSURE"),
        ("max_portfolio_exposure", Decimal(1), "MAX_PORTFOLIO_EXPOSURE"),
        ("multiple", Decimal(1), "MAX_ORDER_VALUE"),
    ],
)
def test_zero_auto_sizing_retains_limiting_reason(tmp_path, monkeypatch, field, value, reason):
    changes = (
        {}
        if field == "cash"
        else (
            {
                "max_order_value": value,
                "max_instrument_exposure": value,
                "max_portfolio_exposure": value,
            }
            if field == "multiple"
            else {field: value}
        )
    )
    db, _, _, r = _build(
        tmp_path,
        monkeypatch,
        cash=value if field == "cash" else Decimal(10000),
        risk=replace(_RISK, **changes),
    )
    r = _changed_intent(r, requested_quantity=None)
    before = _dump(db)
    d = _PortfolioPretradeCoordinator(db).evaluate(r).decisions[0]
    assert d.risk_decision == "BLOCKED" and d.approved_hypothetical_quantity == 0
    assert d.reason_codes == (reason,)
    assert d.reserved_hypothetical_cash == 0 and _dump(db) == before
