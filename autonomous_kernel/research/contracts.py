from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..operations import canonical_hash


RESEARCH_AUTHORITY = {
    "defines_market_truth": False,
    "trains_models": False,
    "promotes_models": False,
    "sets_adaptive_weights": False,
    "capital_decision": False,
    "risk_authorization": False,
    "external_execution": False,
}

DATASET_SCHEMA_VERSION = "1.0"
WALK_FORWARD_SCHEMA_VERSION = "1.0"
EXPERIMENT_SCHEMA_VERSION = "1.0"
MODEL_ARTIFACT_LINEAGE_SCHEMA_VERSION = "1.0"
PROMOTION_ASSESSMENT_SCHEMA_VERSION = "1.0"


class ResearchContractError(ValueError):
    pass


def _digest(value: Any, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ResearchContractError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ResearchContractError("%s must be SHA-256 hex" % field) from exc
    return text


def _strings(values: Sequence[str], field: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if (not result and not allow_empty) or any(not value for value in result):
        raise ResearchContractError("%s must contain non-empty values" % field)
    if len(set(result)) != len(result):
        raise ResearchContractError("%s must contain unique values" % field)
    return result


def _seal(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(body)
    value["integrity"] = {"algorithm": "sha256", "content_hash": canonical_hash(body)}
    return value


def _validate_authority(value: Mapping[str, Any]) -> None:
    if value.get("authority") != RESEARCH_AUTHORITY:
        raise ResearchContractError("research authority boundary changed")


def validate_point_in_time_row(row: Mapping[str, Any]) -> None:
    for field in ("row_id", "question_ref", "question_definition_hash", "subject_id"):
        if not str(row.get(field, "")).strip():
            raise ResearchContractError("dataset row %s is required" % field)
    _digest(row.get("question_definition_hash"), "question_definition_hash")
    cutoff = int(row.get("cutoff_at_ns", -1))
    feature_known = int(row.get("feature_known_at_ns", -1))
    label_known = int(row.get("label_known_at_ns", -1))
    if cutoff < 0 or feature_known < 0 or feature_known > cutoff:
        raise ResearchContractError("dataset row feature evidence must be known by cutoff")
    if label_known <= cutoff:
        raise ResearchContractError("dataset row label must become known strictly after cutoff")
    features = row.get("features")
    context = row.get("context")
    label = row.get("label")
    if not isinstance(features, Mapping) or not features:
        raise ResearchContractError("dataset row requires non-empty features")
    if not isinstance(context, Mapping):
        raise ResearchContractError("dataset row context must be a mapping")
    if label is None:
        raise ResearchContractError("dataset row requires resolved label")
    refs = row.get("source_refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
        raise ResearchContractError("dataset row requires source_refs")
    identities = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            raise ResearchContractError("dataset source ref must be a mapping")
        ref_id = str(ref.get("artifact_id", ""))
        if not ref_id:
            raise ResearchContractError("dataset source ref artifact_id is required")
        identities.append((str(ref.get("artifact_type", "")), ref_id))
        _digest(ref.get("content_hash"), "dataset source content_hash")
        known_at = int(ref.get("known_at_ns", -1))
        role = str(ref.get("role", ""))
        if role == "FEATURE":
            if known_at < 0 or known_at > cutoff:
                raise ResearchContractError("feature source known after cutoff")
        elif role == "LABEL":
            if known_at <= cutoff:
                raise ResearchContractError("label source must be post-cutoff")
        else:
            raise ResearchContractError("dataset source ref role must be FEATURE or LABEL")
    if len(set(identities)) != len(identities):
        raise ResearchContractError("dataset source refs must be unique")
    if not any(str(ref.get("role")) == "FEATURE" for ref in refs):
        raise ResearchContractError("dataset row needs feature lineage")
    if not any(str(ref.get("role")) == "LABEL" for ref in refs):
        raise ResearchContractError("dataset row needs label lineage")


def build_point_in_time_dataset_manifest(
    *,
    dataset_id: str,
    question_ref: str,
    question_definition_hash: str,
    rows: Sequence[Mapping[str, Any]],
    feature_schema_version: str,
    created_at_ns: int,
) -> Mapping[str, Any]:
    if not dataset_id or not question_ref or not feature_schema_version or int(created_at_ns) < 0:
        raise ResearchContractError("dataset identity and timing are required")
    question_hash = _digest(question_definition_hash, "question_definition_hash")
    normalized = [dict(row) for row in rows]
    if not normalized:
        raise ResearchContractError("dataset requires rows")
    seen = set()
    for row in normalized:
        validate_point_in_time_row(row)
        if row["question_ref"] != question_ref or row["question_definition_hash"] != question_hash:
            raise ResearchContractError("dataset row question binding mismatch")
        if row["row_id"] in seen:
            raise ResearchContractError("dataset row_id values must be unique")
        seen.add(row["row_id"])
    normalized.sort(key=lambda item: (int(item["cutoff_at_ns"]), str(item["subject_id"]), str(item["row_id"])))
    body = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "question_ref": question_ref,
        "question_definition_hash": question_hash,
        "feature_schema_version": feature_schema_version,
        "created_at_ns": int(created_at_ns),
        "row_count": len(normalized),
        "rows": normalized,
        "ordering": "CUTOFF_ASC_SUBJECT_ASC_ROW_ID_ASC",
        "lookahead_policy": "FEATURE_KNOWN_AT_OR_BEFORE_CUTOFF_LABEL_STRICTLY_AFTER_CUTOFF",
        "authority": dict(RESEARCH_AUTHORITY),
    }
    return _seal(body)


def validate_dataset_manifest(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ResearchContractError("unsupported dataset schema")
    rows = value.get("rows")
    if not isinstance(rows, list) or value.get("row_count") != len(rows) or not rows:
        raise ResearchContractError("dataset rows/count invalid")
    for row in rows:
        validate_point_in_time_row(row)
    cutoffs = [int(row["cutoff_at_ns"]) for row in rows]
    if cutoffs != sorted(cutoffs):
        raise ResearchContractError("dataset rows must be time ordered")
    _validate_authority(value)
    body = {key: item for key, item in value.items() if key != "integrity"}
    if value.get("integrity", {}).get("content_hash") != canonical_hash(body):
        raise ResearchContractError("dataset content hash mismatch")


def build_walk_forward_plan(
    dataset: Mapping[str, Any],
    *,
    minimum_train_rows: int,
    validation_rows: int,
    step_rows: int,
    embargo_ns: int = 0,
) -> Mapping[str, Any]:
    validate_dataset_manifest(dataset)
    train_n, valid_n, step_n, embargo = int(minimum_train_rows), int(validation_rows), int(step_rows), int(embargo_ns)
    if train_n <= 0 or valid_n <= 0 or step_n <= 0 or embargo < 0:
        raise ResearchContractError("walk-forward sizes must be positive and embargo non-negative")
    rows = dataset["rows"]
    folds = []
    fold_no = 0
    train_end = train_n
    while train_end + valid_n <= len(rows):
        validation_start = train_end
        while validation_start < len(rows) and int(rows[validation_start]["cutoff_at_ns"]) <= int(rows[train_end - 1]["label_known_at_ns"]) + embargo:
            validation_start += 1
        validation_end = validation_start + valid_n
        if validation_end > len(rows):
            break
        train_ids = [row["row_id"] for row in rows[:train_end]]
        valid_ids = [row["row_id"] for row in rows[validation_start:validation_end]]
        folds.append({
            "fold": fold_no,
            "train_row_ids": train_ids,
            "validation_row_ids": valid_ids,
            "train_last_cutoff_ns": int(rows[train_end - 1]["cutoff_at_ns"]),
            "train_last_label_known_at_ns": max(int(row["label_known_at_ns"]) for row in rows[:train_end]),
            "validation_first_cutoff_ns": int(rows[validation_start]["cutoff_at_ns"]),
        })
        fold_no += 1
        train_end += step_n
    if not folds:
        raise ResearchContractError("dataset cannot satisfy walk-forward plan")
    body = {
        "schema_version": WALK_FORWARD_SCHEMA_VERSION,
        "dataset_id": dataset["dataset_id"],
        "dataset_hash": dataset["integrity"]["content_hash"],
        "minimum_train_rows": train_n,
        "validation_rows": valid_n,
        "step_rows": step_n,
        "embargo_ns": embargo,
        "fold_count": len(folds),
        "folds": folds,
        "split_policy": "EXPANDING_WINDOW_STRICT_TIME_ORDER_NO_RANDOM_SHUFFLE",
        "authority": dict(RESEARCH_AUTHORITY),
    }
    return _seal(body)


def validate_walk_forward_plan(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != WALK_FORWARD_SCHEMA_VERSION:
        raise ResearchContractError("unsupported walk-forward schema")
    folds = value.get("folds")
    if not isinstance(folds, list) or not folds or value.get("fold_count") != len(folds):
        raise ResearchContractError("walk-forward folds invalid")
    for fold in folds:
        if not fold.get("train_row_ids") or not fold.get("validation_row_ids"):
            raise ResearchContractError("walk-forward fold requires train and validation rows")
        if set(fold["train_row_ids"]).intersection(fold["validation_row_ids"]):
            raise ResearchContractError("walk-forward train/validation overlap")
        if int(fold["train_last_label_known_at_ns"]) + int(value.get("embargo_ns", 0)) >= int(fold["validation_first_cutoff_ns"]):
            raise ResearchContractError("walk-forward fold leaks unresolved training labels into validation")
    _validate_authority(value)
    body = {key: item for key, item in value.items() if key != "integrity"}
    if value.get("integrity", {}).get("content_hash") != canonical_hash(body):
        raise ResearchContractError("walk-forward content hash mismatch")


def build_experiment_contract(
    *,
    experiment_id: str,
    question_ref: str,
    question_definition_hash: str,
    dataset_hash: str,
    walk_forward_hash: str,
    species: str,
    implementation_ref: str,
    implementation_hash: str,
    hyperparameters: Mapping[str, Any],
    seed: int,
    metric_ids: Sequence[str],
    registered_at_ns: int,
) -> Mapping[str, Any]:
    metrics = _strings(metric_ids, "metric_ids")
    body = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": str(experiment_id),
        "question_ref": str(question_ref),
        "question_definition_hash": _digest(question_definition_hash, "question_definition_hash"),
        "dataset_hash": _digest(dataset_hash, "dataset_hash"),
        "walk_forward_hash": _digest(walk_forward_hash, "walk_forward_hash"),
        "species": str(species),
        "implementation_ref": str(implementation_ref),
        "implementation_hash": _digest(implementation_hash, "implementation_hash"),
        "hyperparameters": dict(hyperparameters),
        "seed": int(seed),
        "metric_ids": list(metrics),
        "registered_at_ns": int(registered_at_ns),
        "training_status": "NOT_RUN",
        "authority": dict(RESEARCH_AUTHORITY),
    }
    if any(not str(body[field]).strip() for field in ("experiment_id", "question_ref", "species", "implementation_ref")) or body["registered_at_ns"] < 0:
        raise ResearchContractError("experiment identity/timing invalid")
    return _seal(body)


def build_model_artifact_lineage(
    experiment: Mapping[str, Any],
    *,
    model_ref: str,
    artifact_hash: str,
    training_code_hash: str,
    fold_receipt_hashes: Sequence[str],
    produced_at_ns: int,
) -> Mapping[str, Any]:
    if experiment.get("schema_version") != EXPERIMENT_SCHEMA_VERSION:
        raise ResearchContractError("model lineage requires experiment contract")
    receipts = tuple(_digest(value, "fold receipt hash") for value in fold_receipt_hashes)
    if not receipts:
        raise ResearchContractError("model lineage requires fold receipts")
    body = {
        "schema_version": MODEL_ARTIFACT_LINEAGE_SCHEMA_VERSION,
        "model_ref": str(model_ref),
        "artifact_hash": _digest(artifact_hash, "artifact_hash"),
        "training_code_hash": _digest(training_code_hash, "training_code_hash"),
        "experiment_id": experiment["experiment_id"],
        "experiment_hash": experiment["integrity"]["content_hash"],
        "dataset_hash": experiment["dataset_hash"],
        "walk_forward_hash": experiment["walk_forward_hash"],
        "question_ref": experiment["question_ref"],
        "question_definition_hash": experiment["question_definition_hash"],
        "fold_receipt_hashes": list(receipts),
        "produced_at_ns": int(produced_at_ns),
        "authority": dict(RESEARCH_AUTHORITY),
    }
    if not body["model_ref"] or body["produced_at_ns"] < int(experiment["registered_at_ns"]):
        raise ResearchContractError("model artifact lineage identity/timing invalid")
    return _seal(body)


def assess_promotion_evidence(
    *,
    model_ref: str,
    question_ref: str,
    evaluation_receipt_hash: str,
    sample_count: int,
    metric_value: float,
    baseline_metric_value: float,
    higher_is_better: bool,
    minimum_samples: int,
    required_improvement: float,
) -> Mapping[str, Any]:
    n = int(sample_count)
    minimum = int(minimum_samples)
    metric = float(metric_value)
    baseline = float(baseline_metric_value)
    delta = metric - baseline if higher_is_better else baseline - metric
    if minimum <= 0 or n < 0 or required_improvement < 0:
        raise ResearchContractError("promotion thresholds invalid")
    if n < minimum:
        decision = "INSUFFICIENT_EVIDENCE"
    elif delta >= float(required_improvement):
        decision = "CANDIDATE_PROMOTION_EVIDENCE_SUPPORTED"
    elif delta < 0:
        decision = "DEGRADATION_EVIDENCE_PRESENT"
    else:
        decision = "NO_PROMOTION_EVIDENCE"
    body = {
        "schema_version": PROMOTION_ASSESSMENT_SCHEMA_VERSION,
        "model_ref": str(model_ref),
        "question_ref": str(question_ref),
        "evaluation_receipt_hash": _digest(evaluation_receipt_hash, "evaluation_receipt_hash"),
        "sample_count": n,
        "metric_value": metric,
        "baseline_metric_value": baseline,
        "higher_is_better": bool(higher_is_better),
        "minimum_samples": minimum,
        "required_improvement": float(required_improvement),
        "observed_improvement": delta,
        "decision": decision,
        "mutates_model_lifecycle": False,
        "authority": dict(RESEARCH_AUTHORITY),
    }
    return _seal(body)
