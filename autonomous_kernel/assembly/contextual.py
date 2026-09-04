from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..context.contracts import MarketContextFrame
from ..operations import canonical_hash
from ..prediction.contracts import Prediction
from ..prediction.factory import create_prediction
from ..representation.contracts import RepresentationFrame
from .contracts import AssemblyReceipt


CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION = "1.0"
CONTEXT_WEIGHT_POLICY_ID = "Z9_CONTEXT_OVERLAY_SHRUNK_BOUNDED_V1"
CONTEXT_MULTIPLIER_MIN = Decimal("0.75")
CONTEXT_MULTIPLIER_MAX = Decimal("1.25")
SUPPORTED_FEATURE_DEPENDENCIES = {"CORE_MARKET", "CROSS_ASSET", "LIQUIDITY", "CORRELATION", "DERIVATIVES", "LEAD_LAG"}
SUPPORTED_REGIME_KEYS = {"direction", "volatility", "liquidity", "correlation", "derivatives", "structure"}
FEATURE_FACTORS = {"QUALIFIED": Decimal("1"), "DEGRADED": Decimal("0.90"), "UNAVAILABLE": Decimal("0.75")}


class ContextualAssemblyError(RuntimeError):
    pass


def _decimal(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ContextualAssemblyError("%s must be decimal-compatible" % field) from exc
    if not number.is_finite():
        raise ContextualAssemblyError("%s must be finite" % field)
    return number


def _digest(value: str, field: str) -> str:
    text = str(value).lower()
    if len(text) != 64:
        raise ContextualAssemblyError("%s must be SHA-256 hex" % field)
    try:
        int(text, 16)
    except ValueError as exc:
        raise ContextualAssemblyError("%s must be hexadecimal" % field) from exc
    return text


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(high, max(low, value))


def _normalize_regime_mapping(value: Mapping[str, Sequence[str]], field: str) -> Mapping[str, Tuple[str, ...]]:
    output: Dict[str, Tuple[str, ...]] = {}
    for key in sorted(value):
        if key not in SUPPORTED_REGIME_KEYS:
            raise ContextualAssemblyError("%s contains unsupported regime key %s" % (field, key))
        values = tuple(sorted({str(item) for item in value[key] if str(item)}))
        if not values:
            raise ContextualAssemblyError("%s[%s] must contain values" % (field, key))
        output[str(key)] = values
    return output


@dataclass(frozen=True)
class ModelContextProfile:
    """Versioned declaration of which Z9 facts are relevant to one model."""

    model_ref: str
    feature_dependencies: Tuple[str, ...]
    preferred_regimes: Mapping[str, Sequence[str]]
    adverse_regimes: Mapping[str, Sequence[str]]
    diversity_group: str
    profile_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.model_ref or not self.profile_version or not self.diversity_group:
            raise ContextualAssemblyError("model context profile identity fields are required")
        dependencies = tuple(sorted(set(str(item) for item in self.feature_dependencies)))
        if len(dependencies) != len(self.feature_dependencies) or any(item not in SUPPORTED_FEATURE_DEPENDENCIES for item in dependencies):
            raise ContextualAssemblyError("feature_dependencies must be unique supported Z9 features")
        _normalize_regime_mapping(self.preferred_regimes, "preferred_regimes")
        _normalize_regime_mapping(self.adverse_regimes, "adverse_regimes")

    def body(self) -> Dict[str, Any]:
        return {
            "profile_version": self.profile_version,
            "model_ref": self.model_ref,
            "feature_dependencies": sorted(self.feature_dependencies),
            "preferred_regimes": {key: list(values) for key, values in _normalize_regime_mapping(self.preferred_regimes, "preferred_regimes").items()},
            "adverse_regimes": {key: list(values) for key, values in _normalize_regime_mapping(self.adverse_regimes, "adverse_regimes").items()},
            "diversity_group": self.diversity_group,
            "authority": "context relevance declaration only; not model promotion or capital authority",
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value


@dataclass(frozen=True)
class ContextualAssemblyReceipt:
    receipt_id: str
    assembly_at_ns: int
    base_assembly_receipt_id: str
    base_assembly_receipt_hash: str
    base_prediction_id: str
    base_prediction_hash: str
    context_id: str
    context_content_hash: str
    context_profile_set_hash: str
    final_prediction_id: str
    final_prediction_hash: str
    contributors: Tuple[Mapping[str, Any], ...]
    weight_policy_id: str = CONTEXT_WEIGHT_POLICY_ID
    schema_version: str = CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION or self.weight_policy_id != CONTEXT_WEIGHT_POLICY_ID:
            raise ContextualAssemblyError("contextual assembly policy/schema is invalid")
        if not self.receipt_id or self.assembly_at_ns < 0:
            raise ContextualAssemblyError("contextual receipt identity/timing is invalid")
        for field in ("base_assembly_receipt_hash", "base_prediction_hash", "context_content_hash", "context_profile_set_hash", "final_prediction_hash"):
            _digest(str(getattr(self, field)), field)
        if not all((self.base_assembly_receipt_id, self.base_prediction_id, self.context_id, self.final_prediction_id)):
            raise ContextualAssemblyError("contextual receipt references are required")
        if len(self.contributors) < 2:
            raise ContextualAssemblyError("contextual assembly requires at least two contributors")
        refs = []
        final_weights = []
        for contributor in self.contributors:
            ref = str(contributor.get("model_ref", ""))
            if not ref:
                raise ContextualAssemblyError("contextual contributor lacks model_ref")
            refs.append(ref)
            _digest(str(contributor.get("component_prediction_hash", "")), "component_prediction_hash")
            _digest(str(contributor.get("context_profile_hash", "")), "context_profile_hash")
            base_weight = _decimal(contributor.get("base_z8_weight"), "base_z8_weight")
            multiplier = _decimal(contributor.get("context_multiplier"), "context_multiplier")
            final_weight = _decimal(contributor.get("final_weight"), "final_weight")
            if base_weight <= 0 or base_weight >= 1 or final_weight <= 0 or final_weight >= 1:
                raise ContextualAssemblyError("contextual contributor weights must lie strictly inside 0..1")
            if multiplier < CONTEXT_MULTIPLIER_MIN or multiplier > CONTEXT_MULTIPLIER_MAX:
                raise ContextualAssemblyError("context multiplier violates bounded policy")
            final_weights.append(final_weight)
        if refs != sorted(refs) or len(refs) != len(set(refs)):
            raise ContextualAssemblyError("contextual contributors must be unique and sorted")
        if sum(final_weights, Decimal("0")) != Decimal("1"):
            raise ContextualAssemblyError("contextual final weights must sum exactly to 1")

    def body(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "assembly_at_ns": int(self.assembly_at_ns),
            "base_z8": {"receipt_id": self.base_assembly_receipt_id, "receipt_hash": self.base_assembly_receipt_hash, "prediction_id": self.base_prediction_id, "prediction_hash": self.base_prediction_hash},
            "z9_context": {"context_id": self.context_id, "content_hash": self.context_content_hash, "profile_set_hash": self.context_profile_set_hash},
            "final_prediction": {"prediction_id": self.final_prediction_id, "content_hash": self.final_prediction_hash},
            "policy": {"weight_policy_id": self.weight_policy_id, "context_multiplier_bounds": {"minimum": format(CONTEXT_MULTIPLIER_MIN, "f"), "maximum": format(CONTEXT_MULTIPLIER_MAX, "f")}, "competence_authority": "INHERITED_FROM_BASE_Z8_RECEIPT", "context_authority": "RELEVANCE_OVERLAY_ONLY"},
            "contributors": [dict(item) for item in self.contributors],
            "authority": {"capital_decision": False, "risk_authorization": False, "external_execution": False},
        }

    def content_hash(self) -> str:
        return canonical_hash(self.body())

    def to_wire(self) -> Dict[str, Any]:
        value = self.body()
        value["integrity"] = {"algorithm": "sha256", "content_hash": self.content_hash()}
        return value

    @classmethod
    def from_wire(cls, value: Mapping[str, Any]) -> "ContextualAssemblyReceipt":
        base = value.get("base_z8")
        context = value.get("z9_context")
        final = value.get("final_prediction")
        policy = value.get("policy")
        contributors = value.get("contributors")
        if not all(isinstance(item, Mapping) for item in (base, context, final, policy)) or not isinstance(contributors, list):
            raise ContextualAssemblyError("contextual receipt envelope is malformed")
        item = cls(
            schema_version=str(value.get("schema_version", "")), receipt_id=str(value.get("receipt_id", "")), assembly_at_ns=int(value.get("assembly_at_ns", -1)),
            base_assembly_receipt_id=str(base.get("receipt_id", "")), base_assembly_receipt_hash=str(base.get("receipt_hash", "")), base_prediction_id=str(base.get("prediction_id", "")), base_prediction_hash=str(base.get("prediction_hash", "")),
            context_id=str(context.get("context_id", "")), context_content_hash=str(context.get("content_hash", "")), context_profile_set_hash=str(context.get("profile_set_hash", "")),
            final_prediction_id=str(final.get("prediction_id", "")), final_prediction_hash=str(final.get("content_hash", "")), contributors=tuple(dict(item) for item in contributors if isinstance(item, Mapping)), weight_policy_id=str(policy.get("weight_policy_id", "")),
        )
        if len(item.contributors) != len(contributors):
            raise ContextualAssemblyError("contextual contributor is malformed")
        authority = value.get("authority")
        if not isinstance(authority, Mapping) or authority.get("capital_decision") is not False or authority.get("risk_authorization") is not False or authority.get("external_execution") is not False:
            raise ContextualAssemblyError("contextual authority boundary is invalid")
        integrity = value.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("content_hash") != item.content_hash():
            raise ContextualAssemblyError("contextual receipt content hash mismatch")
        return item


def _profile_set_hash(profiles: Sequence[ModelContextProfile]) -> str:
    ordered = sorted(profiles, key=lambda profile: profile.model_ref)
    return canonical_hash([{"model_ref": profile.model_ref, "profile_hash": profile.content_hash()} for profile in ordered])


def _validate_base(frame: RepresentationFrame, components: Sequence[Prediction], base_prediction: Prediction, base_receipt: AssemblyReceipt, assembly_at: int) -> Tuple[Prediction, ...]:
    if base_receipt.assembled_prediction_id != base_prediction.prediction_id or base_receipt.assembled_prediction_content_hash != base_prediction.content_hash():
        raise ContextualAssemblyError("base Z8 prediction does not match its receipt")
    if base_receipt.representation_frame_id != frame.frame_id or base_receipt.representation_content_hash != frame.content_hash():
        raise ContextualAssemblyError("base Z8 receipt does not bind the supplied Z2 frame")
    if base_receipt.assembly_at_ns > assembly_at:
        raise ContextualAssemblyError("contextual assembly cannot precede base Z8 assembly")
    ordered = tuple(sorted(components, key=lambda item: item.model_refs))
    component_by_ref = {}
    for component in ordered:
        if len(component.model_refs) != 1:
            raise ContextualAssemblyError("contextual components must each have one model_ref")
        component_by_ref[component.model_refs[0]] = component
    receipt_refs = [str(item.get("model_ref", "")) for item in base_receipt.contributors]
    if receipt_refs != sorted(component_by_ref) or len(receipt_refs) != len(component_by_ref):
        raise ContextualAssemblyError("base Z8 contributor population differs from components")
    for contributor in base_receipt.contributors:
        ref = str(contributor["model_ref"])
        if str(contributor.get("component_prediction_hash")) != component_by_ref[ref].content_hash():
            raise ContextualAssemblyError("base Z8 component hash mismatch for %s" % ref)
    return ordered


def _context_target_member(frame: RepresentationFrame, context: MarketContextFrame, assembly_at: int) -> Mapping[str, Any]:
    if context.known_at_ns > assembly_at or context.cutoff_at_ns > assembly_at:
        raise ContextualAssemblyError("context was not knowable by assembly time")
    members = context.state.get("members")
    member = members.get(frame.instrument.canonical_id) if isinstance(members, Mapping) else None
    if not isinstance(member, Mapping):
        raise ContextualAssemblyError("Z9 context does not contain target instrument")
    if member.get("frame_id") != frame.frame_id or member.get("frame_content_hash") != frame.content_hash():
        raise ContextualAssemblyError("Z9 context does not bind the exact target Z2 frame")
    return member


def _feature_factor(profile: ModelContextProfile, context: MarketContextFrame) -> Tuple[Decimal, Tuple[str, ...]]:
    quality = context.state.get("feature_quality")
    if not profile.feature_dependencies:
        return Decimal("1"), ("NO_CONTEXT_FEATURE_DEPENDENCY",)
    factors = []
    reasons = []
    for feature in sorted(profile.feature_dependencies):
        entry = quality.get(feature) if isinstance(quality, Mapping) else None
        status = str(entry.get("status", "UNAVAILABLE")) if isinstance(entry, Mapping) else "UNAVAILABLE"
        factor = FEATURE_FACTORS.get(status, FEATURE_FACTORS["UNAVAILABLE"])
        factors.append(factor)
        reasons.append("FEATURE_%s_%s" % (feature, status))
    return min(factors), tuple(reasons)


def _regime_factor(profile: ModelContextProfile, context: MarketContextFrame) -> Tuple[Decimal, Tuple[str, ...]]:
    regimes = context.state.get("regimes")
    regimes = regimes if isinstance(regimes, Mapping) else {}
    preferred = _normalize_regime_mapping(profile.preferred_regimes, "preferred_regimes")
    adverse = _normalize_regime_mapping(profile.adverse_regimes, "adverse_regimes")
    factor = Decimal("1")
    reasons = []
    for key in sorted(SUPPORTED_REGIME_KEYS):
        current = str(regimes.get(key, "UNAVAILABLE"))
        if key in adverse and current in adverse[key]:
            factor *= Decimal("0.90")
            reasons.append("REGIME_%s_%s_ADVERSE" % (key.upper(), current))
        elif key in preferred and current in preferred[key]:
            factor *= Decimal("1.05")
            reasons.append("REGIME_%s_%s_PREFERRED" % (key.upper(), current))
    if not reasons:
        reasons.append("REGIME_NEUTRAL")
    return _clamp(factor, Decimal("0.85"), Decimal("1.15")), tuple(reasons)


def contextualize_prediction(frame: RepresentationFrame, components: Sequence[Prediction], base_prediction: Prediction, base_receipt: AssemblyReceipt, context: MarketContextFrame, profiles: Sequence[ModelContextProfile], *, assembly_at_ns: int) -> Tuple[Prediction, ContextualAssemblyReceipt]:
    """Apply a bounded Z9 relevance overlay to an already-valid Z8 assembly."""
    assembly_at = int(assembly_at_ns)
    ordered = _validate_base(frame, components, base_prediction, base_receipt, assembly_at)
    if base_prediction.mode == "PROSPECTIVE_SHADOW" and context.status != "QUALIFIED":
        raise ContextualAssemblyError("prospective contextual assembly requires QUALIFIED Z9 context")
    member = _context_target_member(frame, context, assembly_at)
    profile_map = {profile.model_ref: profile for profile in profiles}
    model_refs = [component.model_refs[0] for component in ordered]
    if sorted(profile_map) != sorted(model_refs) or len(profile_map) != len(profiles):
        raise ContextualAssemblyError("exactly one explicit ModelContextProfile is required per component model")
    group_counts: Dict[str, int] = {}
    for profile in profiles:
        group_counts[profile.diversity_group] = group_counts.get(profile.diversity_group, 0) + 1
    base_by_ref = {str(item["model_ref"]): item for item in base_receipt.contributors}
    component_by_ref = {component.model_refs[0]: component for component in ordered}
    provisional = []
    for model_ref in sorted(model_refs):
        profile = profile_map[model_ref]
        feature_factor, feature_reasons = _feature_factor(profile, context)
        regime_factor, regime_reasons = _regime_factor(profile, context)
        reliability = _clamp(_decimal(member.get("data_reliability", "0"), "data_reliability"), Decimal("0"), Decimal("1"))
        freshness = _clamp(_decimal(member.get("freshness_factor", "0"), "freshness_factor"), Decimal("0"), Decimal("1"))
        reliability_factor = Decimal("0.90") + Decimal("0.10") * reliability
        freshness_factor = Decimal("0.95") + Decimal("0.05") * freshness
        diversity_factor = Decimal("0.95") if group_counts[profile.diversity_group] > 1 else Decimal("1")
        raw_multiplier = feature_factor * regime_factor * reliability_factor * freshness_factor * diversity_factor
        multiplier = _clamp(raw_multiplier, CONTEXT_MULTIPLIER_MIN, CONTEXT_MULTIPLIER_MAX)
        base_weight = _decimal(base_by_ref[model_ref].get("normalized_weight"), "base_z8_weight")
        provisional.append({"model_ref": model_ref, "profile": profile, "component": component_by_ref[model_ref], "base_weight": base_weight, "feature_factor": feature_factor, "regime_factor": regime_factor, "reliability_factor": reliability_factor, "freshness_factor": freshness_factor, "diversity_factor": diversity_factor, "context_multiplier": multiplier, "unnormalized": base_weight * multiplier, "reason_codes": tuple(feature_reasons) + tuple(regime_reasons) + (("DIVERSITY_GROUP_DUPLICATE_PENALTY",) if diversity_factor < 1 else ("DIVERSITY_GROUP_UNIQUE",))})
    total = sum((item["unnormalized"] for item in provisional), Decimal("0"))
    if total <= 0:
        raise ContextualAssemblyError("contextual weight total must be positive")
    running = Decimal("0")
    for index, item in enumerate(provisional):
        item["final_weight"] = Decimal("1") - running if index == len(provisional) - 1 else item["unnormalized"] / total
        running += Decimal("0") if index == len(provisional) - 1 else item["final_weight"]
    expected = sum(_decimal(item["component"].expected_move_bps, "expected_move_bps") * item["final_weight"] for item in provisional)
    probability = sum(_decimal(item["component"].probability_positive, "probability_positive") * item["final_weight"] for item in provisional)
    interval_low = None
    interval_high = None
    if all(item["component"].interval_low_bps is not None and item["component"].interval_high_bps is not None for item in provisional):
        interval_low = min(_decimal(item["component"].interval_low_bps, "interval_low_bps") for item in provisional)
        interval_high = max(_decimal(item["component"].interval_high_bps, "interval_high_bps") for item in provisional)
    profile_set_hash = _profile_set_hash(profiles)
    prediction_id = "CTX-PRED-%s" % canonical_hash({"base_prediction_hash": base_prediction.content_hash(), "context_hash": context.content_hash(), "profile_set_hash": profile_set_hash, "assembly_at_ns": assembly_at, "weights": [(item["model_ref"], format(item["final_weight"], "f")) for item in provisional]})[:32]
    final_prediction = create_prediction(frame, mode=base_prediction.mode, prediction_at_ns=base_prediction.prediction_at_ns, created_at_ns=assembly_at, horizon_ns=base_prediction.horizon_ns, expected_move_bps=expected, probability_positive=probability, interval_low_bps=interval_low, interval_high_bps=interval_high, model_refs=tuple(item["model_ref"] for item in provisional), prediction_id=prediction_id)
    contributors = tuple({"model_ref": item["model_ref"], "component_prediction_id": item["component"].prediction_id, "component_prediction_hash": item["component"].content_hash(), "context_profile_hash": item["profile"].content_hash(), "diversity_group": item["profile"].diversity_group, "base_z8_weight": format(item["base_weight"], "f"), "feature_factor": format(item["feature_factor"], "f"), "regime_factor": format(item["regime_factor"], "f"), "data_reliability_factor": format(item["reliability_factor"], "f"), "freshness_factor": format(item["freshness_factor"], "f"), "diversity_factor": format(item["diversity_factor"], "f"), "context_multiplier": format(item["context_multiplier"], "f"), "final_weight": format(item["final_weight"], "f"), "reason_codes": list(item["reason_codes"])} for item in provisional)
    receipt_material = {"base_receipt_hash": base_receipt.content_hash(), "context_hash": context.content_hash(), "profile_set_hash": profile_set_hash, "final_prediction_hash": final_prediction.content_hash(), "contributors": [dict(item) for item in contributors], "weight_policy_id": CONTEXT_WEIGHT_POLICY_ID}
    receipt = ContextualAssemblyReceipt(receipt_id="CTX-ASM-%s" % canonical_hash(receipt_material)[:32], assembly_at_ns=assembly_at, base_assembly_receipt_id=base_receipt.receipt_id, base_assembly_receipt_hash=base_receipt.content_hash(), base_prediction_id=base_prediction.prediction_id, base_prediction_hash=base_prediction.content_hash(), context_id=context.context_id, context_content_hash=context.content_hash(), context_profile_set_hash=profile_set_hash, final_prediction_id=final_prediction.prediction_id, final_prediction_hash=final_prediction.content_hash(), contributors=contributors)
    return final_prediction, receipt
