from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal

import pytest

import aegis
from aegis.forward_paper_trading import ProposalValidationResult as Phase8ProposalValidationResult
from aegis.proposals import (
    ProposalContract,
    ProposalDecision,
    ProposalValidationResult,
    validate_proposal_contract,
)
from aegis.strategy_lab import StrategySignal


def test_public_validation_result_exports_preserve_phase8_compatibility():
    assert aegis.ProposalValidationResult is Phase8ProposalValidationResult
    assert aegis.ProposalValidationResult(valid=True).valid is True
    assert aegis.ResearchProposalValidationResult is ProposalValidationResult
    assert aegis.ResearchProposalValidationResult is not aegis.ProposalValidationResult
    result = aegis.validate_proposal_contract(_base_payload())
    assert isinstance(result, aegis.ResearchProposalValidationResult)
    assert result.accepted is True


def test_public_package_import_does_not_require_m2_modules():
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import importlib.abc, sys\n"
                "class NoM2(importlib.abc.MetaPathFinder):\n"
                " def find_spec(self, fullname, path=None, target=None):\n"
                "  if fullname.startswith('aegis.portfolio'):\n"
                "   raise ImportError('M2 dependency is prohibited')\n"
                "sys.meta_path.insert(0, NoM2())\n"
                "import aegis\n"
                "assert aegis.ProposalValidationResult(valid=True).valid\n"
                "assert aegis.ResearchProposalValidationResult is not aegis.ProposalValidationResult\n"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _base_payload(**overrides):
    data = {
        "proposal_id": "proposal-123",
        "contract_version": "1.0.0",
        "proposer_id": "test-proposer",
        "proposer_version": "1.0.0",
        "instrument": "NSE:RELIANCE",
        "decision": "BUY",
        "confidence": "0.75",
        "rationale": "The instrument shows a positive trend and valid evidence.",
        "evidence_references": ["dataset:synthetic:1", "indicator:sma:1"],
        "timestamp": "2024-01-10T00:00:00Z",
        "data_window": "2024-01-01T00:00:00Z/2024-01-10T00:00:00Z",
        "proposed_entry": "250.00",
        "proposed_stop": "238.00",
        "proposed_target": "280.00",
        "invalidation_conditions": ["Evidence is stale or missing."],
    }
    data.update(overrides)
    return data


def test_buy_proposal_is_validated_and_round_trips():
    payload = _base_payload()
    proposal = ProposalContract.from_dict(payload)

    assert proposal.decision == ProposalDecision.BUY
    assert proposal.instrument == "NSE:RELIANCE"
    assert proposal.proposed_entry == Decimal("250.00")
    assert proposal.to_dict()["decision"] == "BUY"

    result = validate_proposal_contract(payload)
    assert result.accepted is True
    assert result.evidence_quality_status == "UNKNOWN"
    assert result.proposal is not None
    assert result.proposal.data_as_of == "2024-01-10T00:00:00Z"

    serialized = proposal.to_json()
    round_trip = ProposalContract.from_json(serialized)
    assert round_trip.to_dict() == proposal.to_dict()
    assert json.loads(serialized)["proposal_id"] == proposal.proposal_id


def test_proposal_timestamp_is_normalized_to_utc():
    proposal = ProposalContract.from_dict(_base_payload(timestamp="2024-01-10T05:30:00+05:30"))
    assert proposal.timestamp == "2024-01-10T00:00:00Z"


def test_sell_proposal_is_an_exit_long_intent_without_short_risk_geometry():
    payload = _base_payload(
        decision="SELL",
        proposed_entry=None,
        proposed_stop=None,
        proposed_target=None,
        proposed_exit_price="250.00",
    )
    proposal = ProposalContract.from_dict(payload)
    assert proposal.decision == ProposalDecision.SELL
    assert proposal.proposed_entry is None
    assert proposal.proposed_stop is None
    assert proposal.proposed_target is None
    assert proposal.proposed_exit_price == Decimal("250.00")

    without_price_geometry = ProposalContract.from_dict(
        _base_payload(
            decision="SELL",
            proposed_entry=None,
            proposed_stop=None,
            proposed_target=None,
        )
    )
    assert without_price_geometry.decision == ProposalDecision.SELL


def test_sell_does_not_apply_short_position_stop_or_target_geometry():
    proposal = ProposalContract.from_dict(
        _base_payload(
            decision="SELL",
            proposed_entry="250.00",
            proposed_stop="240.00",
            proposed_target="260.00",
        )
    )
    assert proposal.proposed_stop == Decimal("240.00")
    assert proposal.proposed_target == Decimal("260.00")


def test_hold_proposal_requires_no_entry_stop_or_target():
    proposal = ProposalContract.from_dict(
        _base_payload(
            decision="HOLD",
            proposed_entry=None,
            proposed_stop=None,
            proposed_target=None,
        )
    )
    assert proposal.decision == ProposalDecision.HOLD
    assert proposal.proposed_entry is None
    assert proposal.proposed_stop is None
    assert proposal.proposed_target is None

    with pytest.raises(ValueError, match="HOLD proposals must not include"):
        ProposalContract.from_dict(
            _base_payload(
                decision="HOLD",
                proposed_entry="250.00",
            )
        )


def test_missing_required_fields_are_rejected():
    with pytest.raises(ValueError, match="missing required fields"):
        ProposalContract.from_dict({"instrument": "NSE:RELIANCE"})


@pytest.mark.parametrize("unknown_key", ["unexpected", "proposed_targte"])
def test_unknown_and_misspelled_proposal_fields_are_rejected(unknown_key):
    with pytest.raises(ValueError, match="unknown fields"):
        ProposalContract.from_dict(_base_payload(**{unknown_key: "ignored"}))


def test_supported_legacy_payload_without_new_optional_fields_deserializes():
    payload = _base_payload()
    payload.pop("data_as_of", None)
    payload.pop("proposed_exit_price", None)
    proposal = ProposalContract.from_dict(payload)

    assert proposal.contract_version == "1.0.0"
    assert proposal.data_as_of == "2024-01-10T00:00:00Z"
    assert proposal.proposed_exit_price is None


def test_invalid_confidence_and_nonfinite_numbers():
    with pytest.raises(ValueError, match="confidence"):
        ProposalContract.from_dict(_base_payload(confidence="1.5"))

    with pytest.raises(ValueError, match="must be finite"):
        ProposalContract.from_dict(_base_payload(confidence="NaN"))

    with pytest.raises(ValueError, match="must be finite"):
        ProposalContract.from_dict(_base_payload(confidence="Infinity"))


def test_invalid_timestamps_and_data_windows_are_rejected():
    with pytest.raises(ValueError, match="ISO-8601"):
        ProposalContract.from_dict(_base_payload(timestamp="2024-13-01T00:00:00Z"))

    with pytest.raises(ValueError, match="timezone"):
        ProposalContract.from_dict(_base_payload(timestamp="2024-01-10T00:00:00"))

    with pytest.raises(ValueError, match="end must be later"):
        ProposalContract.from_dict(
            _base_payload(data_window="2024-01-10T00:00:00Z/2024-01-01T00:00:00Z")
        )

    with pytest.raises(ValueError, match="data_window"):
        ProposalContract.from_dict(_base_payload(data_window="not-a-window"))


def test_missing_evidence_is_schema_valid_but_fails_evidence_quality():
    result = validate_proposal_contract(_base_payload(evidence_references=[]))
    assert result.accepted is True
    assert result.evidence_quality_status == "FAIL"


def test_evidence_pass_requires_an_explicit_verification_result():
    payload = _base_payload()
    assert validate_proposal_contract(payload).evidence_quality_status == "UNKNOWN"
    verified = validate_proposal_contract(payload, evidence_verification_status="PASS")
    assert verified.accepted is True
    assert verified.evidence_quality_status == "PASS"
    assert (
        validate_proposal_contract(
            _base_payload(evidence_references=[]),
            evidence_verification_status="PASS",
        ).evidence_quality_status
        == "FAIL"
    )


def test_unsupported_contract_version_is_rejected():
    with pytest.raises(ValueError, match="Unsupported proposal contract version"):
        ProposalContract.from_dict(_base_payload(contract_version="2.0.0"))


def test_validation_result_reports_schema_failures_without_claiming_execution_safety():
    result = validate_proposal_contract({"instrument": "NSE:RELIANCE"})
    assert result.accepted is False
    assert result.proposal is None
    assert result.errors
    assert result.rejection_reasons == result.errors


def test_existing_strategy_signal_can_be_converted_to_common_contract():
    signal = StrategySignal(
        timestamp="2024-01-05T00:00:00Z",
        action="ENTER_LONG",
        close=Decimal("101.50"),
        fast_sma=Decimal("100.00"),
        slow_sma=Decimal("98.50"),
    )
    proposal = ProposalContract.from_strategy_signal(
        instrument="NSE:RELIANCE",
        signal=signal,
        proposer_id="sma-crossover",
        proposer_version="1.0.0",
        rationale="Positive crossover model signal.",
    )
    assert proposal.decision == ProposalDecision.BUY
    assert proposal.proposed_entry == Decimal("101.50")
    assert proposal.confidence is None
    assert proposal.proposed_stop is None
    assert proposal.proposed_target is None
    assert proposal.evidence_references == ()
    assert validate_proposal_contract(proposal).evidence_quality_status == "FAIL"


def test_strategy_exit_signal_maps_only_to_an_exit_price():
    signal = StrategySignal(
        timestamp="2024-01-05T00:00:00Z",
        action="EXIT_LONG",
        close=Decimal("101.50"),
        fast_sma=Decimal("98.50"),
        slow_sma=Decimal("100.00"),
    )
    proposal = ProposalContract.from_strategy_signal(
        instrument="NSE:RELIANCE",
        signal=signal,
        proposer_id="sma-crossover",
        proposer_version="1.0.0",
    )
    assert proposal.decision == ProposalDecision.SELL
    assert proposal.proposed_entry is None
    assert proposal.proposed_exit_price == Decimal("101.50")
    assert proposal.proposed_stop is None
    assert proposal.proposed_target is None


def test_stable_identifier_is_reproducible():
    first = ProposalContract.create(
        proposer_id="test-proposer",
        proposer_version="1.0.0",
        instrument="NSE:RELIANCE",
        decision="BUY",
        confidence="0.8",
        rationale="Stable id test.",
        evidence_references=("dataset:stable:1",),
        timestamp="2024-01-10T00:00:00Z",
        data_window=("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"),
        proposed_entry="250.0",
        proposed_stop="240.0",
        proposed_target="270.0",
    )
    second = ProposalContract.create(
        proposer_id="test-proposer",
        proposer_version="1.0.0",
        instrument="NSE:RELIANCE",
        decision="BUY",
        confidence="0.8",
        rationale="Stable id test.",
        evidence_references=("dataset:stable:1",),
        timestamp="2024-01-10T00:00:00Z",
        data_window=("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"),
        proposed_entry="250.0",
        proposed_stop="240.0",
        proposed_target="270.0",
    )
    assert first.proposal_id == second.proposal_id


def test_evidence_reference_order_is_not_meaningful_and_serialization_is_canonical():
    common = {
        "proposer_id": "test-proposer",
        "proposer_version": "1.0.0",
        "instrument": "NSE:RELIANCE",
        "decision": "HOLD",
        "confidence": None,
        "rationale": "No position change.",
        "timestamp": "2024-01-10T00:00:00Z",
        "data_window": ("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"),
    }
    first = ProposalContract.create(
        **common, evidence_references=("dataset:2", "dataset:1", "dataset:1")
    )
    retry = ProposalContract.create(**common, evidence_references=("dataset:1", "dataset:2"))

    assert first.proposal_id == retry.proposal_id
    assert first.to_dict() == retry.to_dict()
    assert first.evidence_references == ("dataset:1", "dataset:2")


@pytest.mark.parametrize(
    "changes",
    [
        {"proposer_version": "2.0.0"},
        {"evidence_references": ("dataset:stable:2",)},
        {"data_window": ("2024-01-02T00:00:00Z", "2024-01-10T00:00:00Z")},
        {"data_as_of": "2024-01-11T00:00:00Z", "timestamp": "2024-01-11T00:00:00Z"},
        {"confidence": "0.9"},
        {"proposed_entry": "251.0"},
        {"proposed_stop": "239.0"},
        {"proposed_target": "271.0"},
    ],
)
def test_materially_different_proposals_have_distinct_ids(changes):
    base = {
        "proposer_id": "test-proposer",
        "proposer_version": "1.0.0",
        "instrument": "NSE:RELIANCE",
        "decision": "BUY",
        "confidence": "0.8",
        "rationale": "Stable id test.",
        "evidence_references": ("dataset:stable:1",),
        "timestamp": "2024-01-10T00:00:00Z",
        "data_window": ("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"),
        "data_as_of": "2024-01-10T00:00:00Z",
        "proposed_entry": "250.0",
        "proposed_stop": "240.0",
        "proposed_target": "270.0",
    }
    original = ProposalContract.create(**base)
    changed = ProposalContract.create(**(base | changes))
    assert original.proposal_id != changed.proposal_id


def test_proposal_timestamp_may_follow_observation_window():
    proposal = ProposalContract.from_dict(
        _base_payload(
            timestamp="2024-01-12T00:00:00Z",
            data_window="2024-01-01T00:00:00Z/2024-01-10T00:00:00Z",
            data_as_of="2024-01-10T00:00:00Z",
        )
    )
    assert proposal.timestamp == "2024-01-12T00:00:00Z"
    assert proposal.data_as_of == "2024-01-10T00:00:00Z"


def test_observation_window_cannot_extend_beyond_data_as_of():
    with pytest.raises(ValueError, match="data_as_of"):
        ProposalContract.from_dict(
            _base_payload(
                timestamp="2024-01-12T00:00:00Z",
                data_window="2024-01-01T00:00:00Z/2024-01-11T00:00:00Z",
                data_as_of="2024-01-10T00:00:00Z",
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proposal_id", 123),
        ("proposer_id", 123),
        ("proposer_version", 123),
        ("instrument", 123),
        ("rationale", 123),
        ("timestamp", 123),
        ("data_as_of", None),
        ("data_window", 123),
        ("evidence_references", [123]),
        ("confidence", {}),
        ("proposed_entry", []),
        ("invalidation_conditions", "stale"),
    ],
)
def test_invalid_input_types_are_not_coerced(field, value):
    with pytest.raises((TypeError, ValueError)):
        ProposalContract.from_dict(_base_payload(**{field: value}))


def test_direct_string_invalidation_conditions_are_rejected_not_split():
    with pytest.raises(TypeError, match="sequence, not a string"):
        ProposalContract.create(
            proposer_id="test-proposer",
            proposer_version="1.0.0",
            instrument="NSE:RELIANCE",
            decision="HOLD",
            confidence=None,
            rationale="No change.",
            evidence_references=(),
            timestamp="2024-01-10T00:00:00Z",
            data_window=("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z"),
            invalidation_conditions="stale",
        )
    with pytest.raises(TypeError, match="sequence, not a string"):
        ProposalContract(**_base_payload(invalidation_conditions="stale"))
