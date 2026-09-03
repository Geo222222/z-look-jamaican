"""Stable operator-console contracts for ZLJ.

The operator console may request operations. It never acquires capital, risk,
execution, or policy authority merely by rendering a control.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

OPERATOR_SCHEMA_VERSION = "1.0"
CONTROL_STATES = {"AVAILABLE", "UNAVAILABLE", "LOCKED"}
CONTROL_CLASSES = {"READ_ONLY", "MUTATING", "CONSTITUTIONALLY_LOCKED"}


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    label: str
    domain: str
    control_class: str
    state: str
    description: str
    benefit: str
    confirmation_required: bool = False
    parameters: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.command_id or not self.label or not self.domain:
            raise ValueError("operator command identity is required")
        if self.control_class not in CONTROL_CLASSES:
            raise ValueError("unsupported operator control class")
        if self.state not in CONTROL_STATES:
            raise ValueError("unsupported operator control state")
        if self.control_class == "CONSTITUTIONALLY_LOCKED" and self.state != "LOCKED":
            raise ValueError("constitutionally locked commands must be LOCKED")

    def to_wire(self) -> Dict[str, Any]:
        return {
            "schema_version": OPERATOR_SCHEMA_VERSION,
            "command_id": self.command_id,
            "label": self.label,
            "domain": self.domain,
            "control_class": self.control_class,
            "state": self.state,
            "description": self.description,
            "benefit": self.benefit,
            "confirmation_required": self.confirmation_required,
            "parameters": list(self.parameters),
        }


COMMAND_SPECS: Tuple[CommandSpec, ...] = (
    CommandSpec(
        "VALIDATE_KERNEL", "Validate entire kernel", "governance", "READ_ONLY", "AVAILABLE",
        "Run canonical durable-state and Z1-Z9 validation.",
        "Prevents Benjamin from receiving intelligence from a structurally invalid ZLJ state.",
    ),
    CommandSpec(
        "MATERIALIZE_CONTEXT", "Materialize Z9 context", "perception", "MUTATING", "AVAILABLE",
        "Build and persist one authoritative market context at cutoff T from durable Z2 history.",
        "Gives Benjamin context that is point-in-time safe and reproducible rather than caller-selected.",
        True, ("cutoff_at_ns",),
    ),
    CommandSpec(
        "RECOVER_PENDING", "Recover pending state", "operations", "MUTATING", "AVAILABLE",
        "Idempotently roll a prepared kernel transaction forward.",
        "Restores a consistent evidence state after an interrupted operation.",
        True,
    ),
    CommandSpec(
        "FREEZE_STATE", "Freeze current state", "governance", "MUTATING", "UNAVAILABLE",
        "Create a governed immutable construction/evidence checkpoint.",
        "Will preserve historical claims before later architecture or evidence changes.",
        True,
    ),
    CommandSpec(
        "RUN_QUALIFICATION", "Run qualification campaign", "certification", "MUTATING", "UNAVAILABLE",
        "Launch a preregistered qualification campaign from an approved experiment definition.",
        "Will make empirical support visible without allowing dashboard-driven cherry-picking.",
        True,
    ),
    CommandSpec(
        "ACTIVATE_CONTEXT_PROFILE", "Activate model context profile", "models", "MUTATING", "UNAVAILABLE",
        "Activate an already registered ModelContextProfile through its governed event journal.",
        "Will let operators govern contextual interpretation without caller-supplied hidden heuristics.",
        True,
    ),
    CommandSpec(
        "CODE_CHANGE", "Propose source-code change", "development", "MUTATING", "UNAVAILABLE",
        "Create a Git-backed code-change proposal, test it, and promote through repository workflow.",
        "Will let the console operate development without making the browser a production source editor.",
        True,
    ),
    CommandSpec(
        "LIVE_EXECUTION", "Enable live execution", "execution", "CONSTITUTIONALLY_LOCKED", "LOCKED",
        "External order placement is not a ZLJ authority.",
        "Preserves the ZLJ sees → Benjamin decides → Watchman governs → Hand executes boundary.",
        True,
    ),
    CommandSpec(
        "CAPITAL_AUTHORIZATION", "Authorize capital", "risk", "CONSTITUTIONALLY_LOCKED", "LOCKED",
        "Capital authorization is not a ZLJ or frontend authority.",
        "Prevents perception/model tooling from becoming its own risk governor.",
        True,
    ),
    CommandSpec(
        "ORDER_PLACEMENT", "Place order", "execution", "CONSTITUTIONALLY_LOCKED", "LOCKED",
        "Authenticated external action belongs to Hand after Watchman authorization.",
        "Keeps market intelligence separate from external financial action.",
        True,
    ),
)

STAGE_METADATA: Tuple[Mapping[str, Any], ...] = (
    {"id": "Z1", "name": "Observations", "group": "Perception", "purpose": "Turn provider-specific raw market evidence into canonical, provenance-preserving observations.", "benefit": "Benjamin receives intelligence built on traceable, quality-qualified source facts.", "source": "state/canonical_market_data.json"},
    {"id": "Z2", "name": "Representations", "group": "Perception", "purpose": "Convert canonical observations into point-in-time instrument state without lookahead.", "benefit": "Benjamin receives market state rather than raw feed chaos.", "source": "state/representations.json"},
    {"id": "Z3", "name": "Predictions", "group": "Intelligence", "purpose": "Record model claims as durable, falsifiable predictions with explicit horizons and targets.", "benefit": "Benjamin can consume claims that can later be proven right, wrong, or unresolvable.", "source": "state/prediction_journal.json"},
    {"id": "Z4", "name": "Models", "group": "Intelligence", "purpose": "Define immutable model hypotheses that operate on the same representations and prediction contract.", "benefit": "Benjamin is not tied to one opaque forecasting mechanism.", "source": "state/model_registry.json"},
    {"id": "Z5", "name": "Lifecycle", "group": "Governance", "purpose": "Govern model identity, qualification, degradation, quarantine, and succession.", "benefit": "Only models with explicit evidence-backed authority can influence serving intelligence.", "source": "state/model_registry.json"},
    {"id": "Z6", "name": "Outcomes", "group": "Evaluation", "purpose": "Resolve predictions against later qualified market truth using a fixed resolution policy.", "benefit": "Benjamin's upstream intelligence can be judged against reproducible realized outcomes.", "source": "state/outcome_journal.json"},
    {"id": "Z7", "name": "Competence", "group": "Evaluation", "purpose": "Measure where each model is calibrated, accurate, biased, weak, or evidence-starved.", "benefit": "Benjamin receives intelligence weighted by measured competence instead of model prestige.", "source": "memory/outcomes.jsonl"},
    {"id": "Z8", "name": "Assembly", "group": "Intelligence", "purpose": "Assemble specialist predictions with bounded evidence-driven adaptive weights.", "benefit": "Benjamin receives one auditable belief assembled from specialists rather than isolated guesses.", "source": "state/assembly_journal.json"},
    {"id": "Z9", "name": "Market Context", "group": "Perception", "purpose": "Model broader market, liquidity, volatility, correlation, and spot/derivative context around each instrument.", "benefit": "Benjamin receives situationally aware intelligence rather than context-free forecasts.", "source": "state/market_context.json"},
)


def command_catalog() -> Dict[str, Any]:
    return {
        "schema_version": OPERATOR_SCHEMA_VERSION,
        "authority": "operator requests only; domain services and constitutional organs remain authoritative",
        "commands": [spec.to_wire() for spec in COMMAND_SPECS],
    }


def command_spec(command_id: str) -> CommandSpec:
    for spec in COMMAND_SPECS:
        if spec.command_id == command_id:
            return spec
    raise KeyError("unknown operator command: %s" % command_id)
