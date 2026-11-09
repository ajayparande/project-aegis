"""Chronological, provenance-gated ML research utilities; never an execution surface."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Literal

import sklearn  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import accuracy_score, balanced_accuracy_score  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from aegis.candlesticks import Candle, prepare_candles
from aegis.indicators import (
    average_true_range,
    macd,
    rate_of_change,
    relative_strength_index,
    simple_moving_average,
    stochastic_oscillator,
    volume_moving_average,
)

__all__ = [
    "ClassificationMetrics",
    "ExperimentConfig",
    "FeatureValue",
    "MLResearchError",
    "ModelEvaluation",
    "ResearchDataset",
    "ResearchProvenance",
    "ResearchReport",
    "SensitivityPoint",
    "TemporalSplit",
    "WalkForwardFold",
    "build_directional_dataset",
    "chronological_split",
    "run_baseline_experiment",
    "sensitivity_analysis",
    "walk_forward_validate",
]

_RESULT_VERSION = "1.0.0"
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
SourceStatus = Literal["VERIFIED_PERMITTED", "UNVERIFIED", "PROHIBITED", "SYNTHETIC_TEST"]
CalendarStatus = Literal["CONFIRMED", "UNAVAILABLE", "SYNTHETIC_TEST"]
QualityStatus = Literal["PASS", "BLOCKED", "SYNTHETIC_TEST"]


class MLResearchError(ValueError):
    """Raised when an ML research input, split, or evidence gate is invalid."""


@dataclass(frozen=True, slots=True)
class ResearchProvenance:
    """Explicit data source and readiness metadata for one in-memory research set."""

    source_name: str
    source_sha256: str
    source_use_status: SourceStatus
    calendar_status: CalendarStatus
    quality_status: QualityStatus
    instrument: str
    interval: str

    def __post_init__(self) -> None:
        for name in ("source_name", "instrument", "interval"):
            if not getattr(self, name).strip():
                raise MLResearchError(f"{name} must be non-empty.")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256.lower()
        ):
            raise MLResearchError("source_sha256 must be a 64-character hexadecimal digest.")
        synthetic = (
            self.source_use_status == "SYNTHETIC_TEST"
            and self.calendar_status == "SYNTHETIC_TEST"
            and self.quality_status == "SYNTHETIC_TEST"
        )
        permitted = (
            self.source_use_status == "VERIFIED_PERMITTED"
            and self.calendar_status == "CONFIRMED"
            and self.quality_status == "PASS"
        )
        if not synthetic and not permitted:
            raise MLResearchError(
                "Research requires permitted source use, confirmed calendar coverage, and "
                "passing data quality; synthetic fixtures must mark all statuses SYNTHETIC_TEST."
            )

    @property
    def evidence_class(self) -> str:
        return (
            "SYNTHETIC_TEST_ONLY"
            if self.source_use_status == "SYNTHETIC_TEST"
            else "PERMITTED_DATA"
        )

    def to_dict(self) -> dict[str, str]:
        """Return the complete declared provenance in a JSON-safe form."""
        return {
            "source_name": self.source_name,
            "source_sha256": self.source_sha256.lower(),
            "source_use_status": self.source_use_status,
            "calendar_status": self.calendar_status,
            "quality_status": self.quality_status,
            "instrument": self.instrument,
            "interval": self.interval,
        }


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """One feature and the timestamp at which its value was available."""

    name: str
    value: Decimal
    available_at: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise MLResearchError("Feature names must be non-empty.")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise MLResearchError("Features must be finite Decimal values.")
        _timestamp(self.available_at, "feature availability timestamp")


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    """A decision-time feature row and a later, explicitly timestamped binary outcome."""

    decision_timestamp: str
    outcome_timestamp: str
    label: int
    forward_return_pct: Decimal
    features: tuple[FeatureValue, ...]

    def __post_init__(self) -> None:
        decision = _timestamp(self.decision_timestamp, "decision timestamp")
        outcome = _timestamp(self.outcome_timestamp, "outcome timestamp")
        if outcome <= decision:
            raise MLResearchError("Outcome timestamp must follow the feature decision timestamp.")
        if (
            isinstance(self.label, bool)
            or not isinstance(self.label, int)
            or self.label not in (0, 1)
        ):
            raise MLResearchError("label must be integer 0 or 1.")
        if (
            not isinstance(self.forward_return_pct, Decimal)
            or not self.forward_return_pct.is_finite()
        ):
            raise MLResearchError("forward_return_pct must be a finite Decimal.")
        names = [feature.name for feature in self.features]
        if not names or len(names) != len(set(names)):
            raise MLResearchError("Each observation requires unique named features.")
        if any(
            _timestamp(feature.available_at, "feature availability") > decision
            for feature in self.features
        ):
            raise MLResearchError("A feature became available after its decision timestamp.")


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    """Chronological labeled observations with an auditable source declaration."""

    observations: tuple[ResearchObservation, ...]
    provenance: ResearchProvenance
    label_horizon: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.label_horizon, bool)
            or not isinstance(self.label_horizon, int)
            or self.label_horizon < 1
        ):
            raise MLResearchError("label_horizon must be a positive integer.")
        if not self.observations:
            raise MLResearchError("ResearchDataset requires at least one observation.")
        first_names = tuple(feature.name for feature in self.observations[0].features)
        previous: datetime | None = None
        for observation in self.observations:
            current = _timestamp(observation.decision_timestamp, "decision timestamp")
            if previous is not None and current <= previous:
                raise MLResearchError("Observations must be strictly chronological and unique.")
            if tuple(feature.name for feature in observation.features) != first_names:
                raise MLResearchError("Every observation must use the same ordered feature schema.")
            previous = current

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.observations[0].features)

    @property
    def sha256(self) -> str:
        document = {
            "provenance": self.provenance.to_dict(),
            "label_horizon": self.label_horizon,
            "observations": [
                {
                    "decision_timestamp": row.decision_timestamp,
                    "outcome_timestamp": row.outcome_timestamp,
                    "label": row.label,
                    "forward_return_pct": _decimal_text(row.forward_return_pct),
                    "features": [
                        (item.name, _decimal_text(item.value), item.available_at)
                        for item in row.features
                    ],
                }
                for row in self.observations
            ],
        }
        encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    """Chronological train/validation/test partitions with overlapping labels purged."""

    train: tuple[ResearchObservation, ...]
    validation: tuple[ResearchObservation, ...]
    test: tuple[ResearchObservation, ...]
    purged_train: int
    purged_validation: int


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Frozen logistic-regression and chronological split parameters."""

    train_fraction: Decimal = Decimal("0.60")
    validation_fraction: Decimal = Decimal("0.20")
    regularization_c: Decimal = Decimal(1)
    max_iter: int = 1000
    random_state: int = 0

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in (self.train_fraction, self.validation_fraction, self.regularization_c)
        ):
            raise MLResearchError("Experiment decimal parameters must be finite Decimal values.")
        if not (Decimal(0) < self.train_fraction < Decimal(1)):
            raise MLResearchError("train_fraction must be between zero and one.")
        if not (Decimal(0) < self.validation_fraction < Decimal(1)):
            raise MLResearchError("validation_fraction must be between zero and one.")
        if self.train_fraction + self.validation_fraction >= Decimal(1):
            raise MLResearchError("train and validation fractions must leave a test partition.")
        if self.regularization_c <= 0:
            raise MLResearchError("regularization_c must be positive.")
        if (
            isinstance(self.max_iter, bool)
            or not isinstance(self.max_iter, int)
            or self.max_iter < 1
        ):
            raise MLResearchError("max_iter must be a positive integer.")
        if isinstance(self.random_state, bool) or not isinstance(self.random_state, int):
            raise MLResearchError("random_state must be an integer.")


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    count: int
    accuracy: Decimal
    balanced_accuracy: Decimal

    def to_dict(self) -> dict[str, int | str]:
        return {
            "count": self.count,
            "accuracy": _decimal_text(self.accuracy),
            "balanced_accuracy": _decimal_text(self.balanced_accuracy),
        }


@dataclass(frozen=True, slots=True)
class ModelEvaluation:
    split: str
    benchmark: str
    metrics: ClassificationMetrics

    def to_dict(self) -> dict[str, Any]:
        return {"split": self.split, "benchmark": self.benchmark, **self.metrics.to_dict()}


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """Metrics and provenance for one frozen, descriptive classifier experiment."""

    dataset_sha256: str
    evidence_class: str
    provenance: ResearchProvenance
    feature_names: tuple[str, ...]
    model_specification: Mapping[str, Any]
    split_timestamps: Mapping[str, str]
    evaluations: tuple[ModelEvaluation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_schema_version": _RESULT_VERSION,
            "dataset_sha256": self.dataset_sha256,
            "evidence_class": self.evidence_class,
            "provenance": self.provenance.to_dict(),
            "feature_names": list(self.feature_names),
            "model_specification": dict(self.model_specification),
            "split_timestamps": dict(self.split_timestamps),
            "evaluations": [item.to_dict() for item in self.evaluations],
            "interpretation": "classification research metrics only; not trading performance or a signal",
        }


@dataclass(frozen=True, slots=True)
class SensitivityPoint:
    regularization_c: Decimal
    validation_metrics: ClassificationMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_index: int
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    model_metrics: ClassificationMetrics
    majority_metrics: ClassificationMetrics
    sma_rule_metrics: ClassificationMetrics


def build_directional_dataset(
    candles: Sequence[Candle | dict[str, Any] | tuple[Any, ...]],
    *,
    provenance: ResearchProvenance,
    label_horizon: int = 1,
    normalize: bool = False,
) -> ResearchDataset:
    """Build decision-time features from Phase 6 indicators and future-direction labels.

    The label is 1 when the close ``label_horizon`` supplied observations later is
    above the decision close, and 0 otherwise. The future close is label-only; every
    feature is available at the decision candle close. No candles are synthesized.
    """
    if isinstance(label_horizon, bool) or not isinstance(label_horizon, int) or label_horizon < 1:
        raise MLResearchError("label_horizon must be a positive integer.")
    prepared = prepare_candles(candles, normalize=normalize)
    if len(prepared) <= 50 + label_horizon:
        raise MLResearchError(
            "Insufficient candles for the default feature warm-up and label horizon."
        )

    sma_20 = simple_moving_average(prepared, 20).columns["sma"]
    sma_50 = simple_moving_average(prepared, 50).columns["sma"]
    rsi_14 = relative_strength_index(prepared, 14).columns["rsi"]
    macd_line = macd(prepared).columns["macd"]
    roc_12 = rate_of_change(prepared, 12).columns["roc"]
    atr_14 = average_true_range(prepared, 14).columns["atr"]
    stochastic_k = stochastic_oscillator(prepared).columns["k"]
    volume_ma_20 = volume_moving_average(prepared, 20).columns["volume_ma"]

    observations: list[ResearchObservation] = []
    with localcontext(_DECIMAL_CONTEXT):
        for index in range(50, len(prepared) - label_horizon):
            current = prepared[index]
            future = prepared[index + label_horizon]
            indicators = (
                sma_20[index],
                sma_50[index],
                rsi_14[index],
                macd_line[index],
                roc_12[index],
                atr_14[index],
                stochastic_k[index],
                volume_ma_20[index],
            )
            if any(value is None for value in indicators) or volume_ma_20[index] == 0:
                continue
            avg20, avg50, rsi, macd_value, roc, atr, stochastic, volume_average = indicators
            assert avg20 is not None
            assert avg50 is not None
            assert rsi is not None
            assert macd_value is not None
            assert roc is not None
            assert atr is not None
            assert stochastic is not None
            assert volume_average is not None
            forward_return = Decimal(100) * (future.close / current.close - Decimal(1))
            feature_values = (
                ("close_vs_sma20_pct", Decimal(100) * (current.close / avg20 - Decimal(1))),
                ("sma20_vs_sma50_pct", Decimal(100) * (avg20 / avg50 - Decimal(1))),
                ("rsi14", rsi),
                ("macd_to_close_pct", Decimal(100) * macd_value / current.close),
                ("roc12_pct", roc),
                ("atr14_to_close_pct", Decimal(100) * atr / current.close),
                ("stochastic_k", stochastic),
                ("volume_ratio20", Decimal(current.volume) / volume_average),
            )
            features = tuple(
                FeatureValue(name, value, current.timestamp) for name, value in feature_values
            )
            observations.append(
                ResearchObservation(
                    decision_timestamp=current.timestamp,
                    outcome_timestamp=future.timestamp,
                    label=int(future.close > current.close),
                    forward_return_pct=forward_return,
                    features=features,
                )
            )
    if len(observations) < 10:
        raise MLResearchError(
            "Insufficient complete feature/label rows for chronological evaluation."
        )
    return ResearchDataset(tuple(observations), provenance, label_horizon)


def chronological_split(
    dataset: ResearchDataset,
    *,
    train_fraction: Decimal = Decimal("0.60"),
    validation_fraction: Decimal = Decimal("0.20"),
) -> TemporalSplit:
    """Split chronologically and purge rows whose future labels reach the next period."""
    if not isinstance(train_fraction, Decimal) or not isinstance(validation_fraction, Decimal):
        raise MLResearchError("Split fractions must be Decimal values.")
    if (
        not train_fraction.is_finite()
        or not validation_fraction.is_finite()
        or train_fraction <= 0
        or validation_fraction <= 0
        or train_fraction + validation_fraction >= 1
    ):
        raise MLResearchError("Split fractions must be positive and leave a non-empty test period.")
    rows = dataset.observations
    train_end = int((Decimal(len(rows)) * train_fraction).to_integral_value(rounding=ROUND_FLOOR))
    validation_end = int(
        (Decimal(len(rows)) * (train_fraction + validation_fraction)).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    if train_end < 2 or validation_end <= train_end or validation_end >= len(rows):
        raise MLResearchError("Dataset is too small for non-empty chronological partitions.")
    train_boundary = _timestamp(rows[train_end].decision_timestamp, "validation boundary")
    validation_boundary = _timestamp(rows[validation_end].decision_timestamp, "test boundary")
    train_candidates = rows[:train_end]
    validation_candidates = rows[train_end:validation_end]
    test_rows = rows[validation_end:]
    train = tuple(
        row
        for row in train_candidates
        if _timestamp(row.outcome_timestamp, "outcome timestamp") < train_boundary
    )
    validation = tuple(
        row
        for row in validation_candidates
        if _timestamp(row.outcome_timestamp, "outcome timestamp") < validation_boundary
    )
    if not train or not validation or not test_rows:
        raise MLResearchError("Purging overlapping labels left an empty chronological partition.")
    return TemporalSplit(
        train=train,
        validation=validation,
        test=tuple(test_rows),
        purged_train=len(train_candidates) - len(train),
        purged_validation=len(validation_candidates) - len(validation),
    )


def run_baseline_experiment(
    dataset: ResearchDataset, config: ExperimentConfig | None = None
) -> ResearchReport:
    """Fit a fixed logistic baseline and compare it with majority/SMA rule baselines.

    The test partition is evaluated once after the caller supplies a frozen config.
    Metrics are classifier diagnostics only; this API does not simulate trades, costs,
    slippage, strategy promotion, or model persistence.
    """
    config = config or ExperimentConfig()
    evidence_class = _require_evidence_gate(dataset.provenance)
    split = chronological_split(
        dataset,
        train_fraction=config.train_fraction,
        validation_fraction=config.validation_fraction,
    )
    train_x, train_y = _matrix(split.train, dataset.feature_names)
    if len(set(train_y)) != 2:
        raise MLResearchError("Training partition must contain both binary classes.")
    estimator = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=float(config.regularization_c),
            class_weight="balanced",
            max_iter=config.max_iter,
            random_state=config.random_state,
            solver="lbfgs",
        ),
    )
    estimator.fit(train_x, train_y)
    majority_label = Counter(train_y).most_common()
    majority = min(label for label, count in majority_label if count == majority_label[0][1])
    evaluations: list[ModelEvaluation] = []
    for split_name, rows in (
        ("train", split.train),
        ("validation", split.validation),
        ("test", split.test),
    ):
        matrix, actual = _matrix(rows, dataset.feature_names)
        model_predictions = estimator.predict(matrix).tolist()
        majority_predictions = [majority] * len(rows)
        sma_predictions = [int(_feature(row, "sma20_vs_sma50_pct") > 0) for row in rows]
        evaluations.extend(
            (
                ModelEvaluation(
                    split_name, "logistic_regression", _metrics(actual, model_predictions)
                ),
                ModelEvaluation(
                    split_name, "training_majority", _metrics(actual, majority_predictions)
                ),
                ModelEvaluation(
                    split_name, "sma20_vs_sma50_rule", _metrics(actual, sma_predictions)
                ),
            )
        )
    return ResearchReport(
        dataset_sha256=dataset.sha256,
        evidence_class=evidence_class,
        provenance=dataset.provenance,
        feature_names=dataset.feature_names,
        model_specification={
            "algorithm": "StandardScaler+LogisticRegression",
            "scikit_learn_version": sklearn.__version__,
            "regularization_c": _decimal_text(config.regularization_c),
            "class_weight": "balanced",
            "max_iter": config.max_iter,
            "random_state": config.random_state,
            "fit_partition": "train_only",
            "target": "future_close_above_decision_close",
            "target_horizon_observations": dataset.label_horizon,
            "classifier_metrics_only": True,
            "costs_evaluated": False,
        },
        split_timestamps={
            "train_first": split.train[0].decision_timestamp,
            "train_last_outcome": split.train[-1].outcome_timestamp,
            "validation_first": split.validation[0].decision_timestamp,
            "validation_last_outcome": split.validation[-1].outcome_timestamp,
            "test_first": split.test[0].decision_timestamp,
            "test_last_outcome": split.test[-1].outcome_timestamp,
            "purged_train_rows": str(split.purged_train),
            "purged_validation_rows": str(split.purged_validation),
        },
        evaluations=tuple(evaluations),
    )


def sensitivity_analysis(
    dataset: ResearchDataset,
    regularization_values: Sequence[Decimal] = (
        Decimal("0.1"),
        Decimal(1),
        Decimal(10),
    ),
    *,
    config: ExperimentConfig | None = None,
) -> tuple[SensitivityPoint, ...]:
    """Evaluate fixed C values on validation only; this function never reads test rows."""
    config = config or ExperimentConfig()
    _require_evidence_gate(dataset.provenance)
    if not regularization_values or len(set(regularization_values)) != len(regularization_values):
        raise MLResearchError("Sensitivity values must be non-empty and unique.")
    split = chronological_split(
        dataset,
        train_fraction=config.train_fraction,
        validation_fraction=config.validation_fraction,
    )
    train_x, train_y = _matrix(split.train, dataset.feature_names)
    validation_x, validation_y = _matrix(split.validation, dataset.feature_names)
    if len(set(train_y)) != 2:
        raise MLResearchError("Training partition must contain both binary classes.")
    points: list[SensitivityPoint] = []
    for c_value in regularization_values:
        if not isinstance(c_value, Decimal) or not c_value.is_finite() or c_value <= 0:
            raise MLResearchError("Every sensitivity C value must be a positive Decimal.")
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=float(c_value),
                class_weight="balanced",
                max_iter=config.max_iter,
                random_state=config.random_state,
                solver="lbfgs",
            ),
        )
        model.fit(train_x, train_y)
        points.append(
            SensitivityPoint(c_value, _metrics(validation_y, model.predict(validation_x).tolist()))
        )
    return tuple(points)


def walk_forward_validate(
    dataset: ResearchDataset,
    *,
    initial_train_size: int,
    validation_size: int,
    folds: int,
    config: ExperimentConfig | None = None,
) -> tuple[WalkForwardFold, ...]:
    """Run expanding-window validation folds with purged future labels."""
    config = config or ExperimentConfig()
    _require_evidence_gate(dataset.provenance)
    for name, value in (
        ("initial_train_size", initial_train_size),
        ("validation_size", validation_size),
        ("folds", folds),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MLResearchError(f"{name} must be a positive integer.")
    rows = dataset.observations
    if initial_train_size + validation_size * folds > len(rows):
        raise MLResearchError("Walk-forward windows exceed available observations.")
    results: list[WalkForwardFold] = []
    for fold_index in range(folds):
        train_stop = initial_train_size + fold_index * validation_size
        validation_stop = train_stop + validation_size
        validation_candidates = rows[train_stop:validation_stop]
        boundary = _timestamp(validation_candidates[0].decision_timestamp, "fold boundary")
        train_rows = tuple(
            row
            for row in rows[:train_stop]
            if _timestamp(row.outcome_timestamp, "outcome timestamp") < boundary
        )
        validation_rows = (
            tuple(
                row
                for row in validation_candidates
                if _timestamp(row.outcome_timestamp, "outcome timestamp")
                < _timestamp(rows[validation_stop].decision_timestamp, "next fold boundary")
            )
            if validation_stop < len(rows)
            else validation_candidates
        )
        if not train_rows or not validation_rows:
            raise MLResearchError("Walk-forward purging left an empty train or validation fold.")
        train_x, train_y = _matrix(train_rows, dataset.feature_names)
        validation_x, validation_y = _matrix(validation_rows, dataset.feature_names)
        if len(set(train_y)) != 2:
            raise MLResearchError(f"Walk-forward fold {fold_index} lacks both training classes.")
        model = _new_model(config)
        model.fit(train_x, train_y)
        majority = _majority_label(train_y)
        results.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_start=train_rows[0].decision_timestamp,
                train_end=train_rows[-1].outcome_timestamp,
                validation_start=validation_rows[0].decision_timestamp,
                validation_end=validation_rows[-1].outcome_timestamp,
                model_metrics=_metrics(validation_y, model.predict(validation_x).tolist()),
                majority_metrics=_metrics(validation_y, [majority] * len(validation_rows)),
                sma_rule_metrics=_metrics(
                    validation_y,
                    [int(_feature(row, "sma20_vs_sma50_pct") > 0) for row in validation_rows],
                ),
            )
        )
    return tuple(results)


def _new_model(config: ExperimentConfig):
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=float(config.regularization_c),
            class_weight="balanced",
            max_iter=config.max_iter,
            random_state=config.random_state,
            solver="lbfgs",
        ),
    )


def _require_evidence_gate(provenance: ResearchProvenance) -> str:
    if provenance.evidence_class == "SYNTHETIC_TEST_ONLY":
        return provenance.evidence_class
    if provenance.evidence_class != "PERMITTED_DATA":
        raise MLResearchError("Unverified or quality-blocked inputs cannot be evaluated.")
    return provenance.evidence_class


def _matrix(
    rows: Sequence[ResearchObservation], feature_names: Sequence[str]
) -> tuple[list[list[float]], list[int]]:
    matrix: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        by_name = {feature.name: feature.value for feature in row.features}
        values = [float(by_name[name]) for name in feature_names]
        if not all(math.isfinite(value) for value in values):
            raise MLResearchError("Feature conversion exceeded finite model input range.")
        matrix.append(values)
        labels.append(row.label)
    return matrix, labels


def _metrics(actual: Sequence[int], predicted: Sequence[int]) -> ClassificationMetrics:
    return ClassificationMetrics(
        count=len(actual),
        accuracy=Decimal(str(accuracy_score(actual, predicted))),
        balanced_accuracy=Decimal(str(balanced_accuracy_score(actual, predicted))),
    )


def _majority_label(labels: Sequence[int]) -> int:
    counts = Counter(labels)
    maximum = max(counts.values())
    return min(label for label, count in counts.items() if count == maximum)


def _feature(row: ResearchObservation, name: str) -> Decimal:
    for feature in row.features:
        if feature.name == name:
            return feature.value
    raise MLResearchError(f"Required rule-baseline feature {name!r} is unavailable.")


def _timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise MLResearchError(f"{label} must be an ISO-8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MLResearchError(f"{label} must be a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLResearchError(f"{label} must include a UTC offset.")
    return parsed.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result
