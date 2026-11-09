"""Synthetic-only verification of chronological ML research safeguards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from aegis.candlesticks import Candle
from aegis.ml_research import (
    FeatureValue,
    MLResearchError,
    ResearchDataset,
    ResearchObservation,
    ResearchProvenance,
    build_directional_dataset,
    chronological_split,
    run_baseline_experiment,
    sensitivity_analysis,
    walk_forward_validate,
)


def _provenance() -> ResearchProvenance:
    return ResearchProvenance(
        source_name="deterministic unit-test fixture",
        source_sha256=sha256(b"phase 7 synthetic fixture").hexdigest(),
        source_use_status="SYNTHETIC_TEST",
        calendar_status="SYNTHETIC_TEST",
        quality_status="SYNTHETIC_TEST",
        instrument="TEST",
        interval="1h",
    )


def _dataset(count: int = 120) -> ResearchDataset:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(count):
        decision = start + timedelta(hours=index)
        outcome = decision + timedelta(hours=1)
        label = int(index % 4 in (1, 2))
        trend = Decimal(1 if label else -1) * Decimal("0.5")
        rows.append(
            ResearchObservation(
                decision_timestamp=decision.isoformat(),
                outcome_timestamp=outcome.isoformat(),
                label=label,
                forward_return_pct=Decimal(1) if label else Decimal(-1),
                features=(
                    FeatureValue("signal", trend, decision.isoformat()),
                    FeatureValue("sma20_vs_sma50_pct", trend, decision.isoformat()),
                ),
            )
        )
    return ResearchDataset(tuple(rows), _provenance())


def _candles(count: int = 100) -> list[Candle]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    result = []
    price = Decimal(100)
    for index in range(count):
        price += Decimal("0.4") if index % 9 < 5 else Decimal("-0.25")
        result.append(
            Candle.from_values(
                (start + timedelta(hours=index)).isoformat(),
                price,
                price + Decimal("0.5"),
                price - Decimal("0.5"),
                price,
                100 + index,
            )
        )
    return result


def test_dataset_builder_uses_completed_candles_and_decision_time_features() -> None:
    candles = _candles()
    original = tuple(candles)
    dataset = build_directional_dataset(candles, provenance=_provenance(), label_horizon=2)
    truncated = build_directional_dataset(candles[:-5], provenance=_provenance(), label_horizon=2)

    assert dataset.label_horizon == 2
    assert dataset.feature_names == (
        "close_vs_sma20_pct",
        "sma20_vs_sma50_pct",
        "rsi14",
        "macd_to_close_pct",
        "roc12_pct",
        "atr14_to_close_pct",
        "stochastic_k",
        "volume_ratio20",
    )
    assert dataset.observations[0].decision_timestamp == candles[50].timestamp
    assert dataset.observations[0].outcome_timestamp == candles[52].timestamp
    assert all(
        feature.available_at == row.decision_timestamp
        for row in dataset.observations
        for feature in row.features
    )
    common = {row.decision_timestamp: row for row in truncated.observations}
    assert all(
        row == common[row.decision_timestamp]
        for row in dataset.observations
        if row.decision_timestamp in common
    )
    assert tuple(candles) == original


def test_research_provenance_fails_closed_for_unverified_data() -> None:
    with pytest.raises(MLResearchError, match="Research requires"):
        ResearchProvenance(
            source_name="unverified fixture",
            source_sha256=sha256(b"fixture").hexdigest(),
            source_use_status="UNVERIFIED",
            calendar_status="UNAVAILABLE",
            quality_status="BLOCKED",
            instrument="TEST",
            interval="1h",
        )


def test_feature_timestamp_cannot_exceed_decision_time() -> None:
    decision = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(MLResearchError, match="became available"):
        ResearchObservation(
            decision_timestamp=decision.isoformat(),
            outcome_timestamp=(decision + timedelta(hours=2)).isoformat(),
            label=1,
            forward_return_pct=Decimal(1),
            features=(
                FeatureValue("leak", Decimal(1), (decision + timedelta(hours=1)).isoformat()),
            ),
        )


def test_chronological_split_purges_overlapping_outcomes() -> None:
    split = chronological_split(
        _dataset(), train_fraction=Decimal("0.6"), validation_fraction=Decimal("0.2")
    )

    assert split.train[-1].outcome_timestamp < split.validation[0].decision_timestamp
    assert split.validation[-1].outcome_timestamp < split.test[0].decision_timestamp
    assert split.purged_train == 1
    assert split.purged_validation == 1
    assert [row.decision_timestamp for row in split.train] == sorted(
        row.decision_timestamp for row in split.train
    )


def test_model_run_is_deterministic_and_reports_only_classifier_diagnostics() -> None:
    dataset = _dataset()
    first = run_baseline_experiment(dataset)
    second = run_baseline_experiment(dataset)

    assert first.to_dict() == second.to_dict()
    assert first.evidence_class == "SYNTHETIC_TEST_ONLY"
    assert first.to_dict()["provenance"] == dataset.provenance.to_dict()
    assert first.dataset_sha256 == dataset.sha256
    assert first.model_specification["target_horizon_observations"] == 1
    assert first.model_specification["costs_evaluated"] is False
    assert {item.split for item in first.evaluations} == {"train", "validation", "test"}
    assert {item.benchmark for item in first.evaluations} == {
        "logistic_regression",
        "training_majority",
        "sma20_vs_sma50_rule",
    }


def test_sensitivity_is_validation_only_and_walk_forward_is_chronological() -> None:
    dataset = _dataset()
    sensitivity = sensitivity_analysis(dataset, (Decimal("0.1"), Decimal(1)))
    folds = walk_forward_validate(dataset, initial_train_size=60, validation_size=15, folds=3)

    assert len(sensitivity) == 2
    assert all(point.validation_metrics.count > 0 for point in sensitivity)
    assert [fold.fold_index for fold in folds] == [0, 1, 2]
    assert all(fold.train_end < fold.validation_start for fold in folds)
    assert all(fold.validation_start <= fold.validation_end for fold in folds)


def test_dataset_builder_rejects_incomplete_or_insufficient_candles() -> None:
    candles = _candles()
    with pytest.raises(ValueError):
        Candle.from_values(
            candles[-1].timestamp,
            candles[-1].open,
            candles[-1].high,
            candles[-1].low,
            candles[-1].close,
            candles[-1].volume,
            is_complete=False,
        )
    with pytest.raises(MLResearchError, match="Insufficient candles"):
        build_directional_dataset(candles[:40], provenance=_provenance())
    with pytest.raises(MLResearchError, match="label_horizon"):
        build_directional_dataset(candles, provenance=_provenance(), label_horizon=0)
