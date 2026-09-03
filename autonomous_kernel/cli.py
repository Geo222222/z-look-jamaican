"""Command-line interface for deterministic kernel state operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .assembly.context_profile_registry import ModelContextProfileRegistry, profile_from_mapping, validate_model_context_profile_registry
from .assembly.contextual_journal import validate_contextual_assembly_journal
from .assembly.contextual_lineage import validate_contextual_assembly_lineage
from .assembly.journal import validate_assembly_journal
from .assembly.lineage import validate_assembly_lineage
from .context.materialize import materialize_market_context, validate_market_context_materializations
from .context.status import market_context_status
from .context.store import validate_market_context_store
from .evaluation.journal import validate_outcome_journal
from .models.registry import validate_model_registry
from .monitor import monitor_snapshot
from .predecessor import PredecessorVerificationError, verify_manifest
from .qualified_shadow import ShadowDecisionProposal, record_qualified_shadow_decision
from .store import StateValidationError, next_work, recover_pending, repository_root, status_summary, transition, update_task, validate


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def _validate_learning_state_or_raise(root: Path) -> Sequence[str]:
    checks = []
    validators = (
        ("model_registry", validate_model_registry),
        ("model_context_profile_registry", validate_model_context_profile_registry),
        ("outcome_journal", validate_outcome_journal),
        ("assembly_journal", validate_assembly_journal),
        ("assembly_lineage", validate_assembly_lineage),
        ("market_context_store", validate_market_context_store),
        ("market_context_materializations", validate_market_context_materializations),
        ("contextual_assembly_journal", validate_contextual_assembly_journal),
        ("contextual_assembly_lineage", validate_contextual_assembly_lineage),
    )
    for name, validator in validators:
        errors = validator(root)
        if errors:
            raise StateValidationError(errors)
        checks.append(name)
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autokernel", description="Operate validated durable autonomous state")
    parser.add_argument("--root", type=Path, default=repository_root(), help="repository root (defaults to package root)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate schemas, references, Governor snapshots, governed learning state, and Z9 context")
    subparsers.add_parser("status", help="show a machine-readable resume summary")
    subparsers.add_parser("context_status", help="show read-only Z9 market-context status")
    context_materialize_parser = subparsers.add_parser("context_materialize", help="canonically materialize one durable point-in-time Z9 context from Z2 history")
    context_materialize_parser.add_argument("--cutoff-at-ns", type=int, required=True)
    profile_parser = subparsers.add_parser("context_profile_register", help="register one immutable governed ModelContextProfile bound to an existing model")
    profile_parser.add_argument("--profile", type=Path, required=True, help="JSON profile declaration")
    profile_parser.add_argument("--registered-at-ns", type=int, required=True)
    profile_parser.add_argument("--evidence", action="append", required=True, help="governance evidence reference; repeat for multiple refs")
    subparsers.add_parser("next-work", help="select the highest-scored ready task whose dependencies are complete")
    subparsers.add_parser("recover", help="idempotently roll a prepared state transaction forward")
    monitor_parser = subparsers.add_parser("monitor_snapshot", help="emit the authoritative read-only observer snapshot")
    monitor_parser.add_argument("--json", action="store_true", help="emit JSON (the only supported representation)")
    predecessor_parser = subparsers.add_parser("predecessor_verify", help="verify hashed predecessor evidence without importing or executing it")
    predecessor_parser.add_argument("--manifest", type=Path, required=True)
    predecessor_parser.add_argument("--source-root", type=Path, required=True)
    transition_parser = subparsers.add_parser("transition", help="record an allowed root-state transition")
    transition_parser.add_argument("--to", required=True, dest="new_state")
    transition_parser.add_argument("--trigger", required=True)
    transition_parser.add_argument("--decision-id", required=True)
    transition_parser.add_argument("--evidence", action="append", required=True)
    task_parser = subparsers.add_parser("task-status", help="update a backlog task and refresh the resume pointer")
    task_parser.add_argument("--task-id", required=True)
    task_parser.add_argument("--status", required=True)
    qualified_shadow_parser = subparsers.add_parser("qualified_shadow_record", help="persist one prospective zero-capital shadow decision bound to qualified market observations")
    qualified_shadow_parser.add_argument("--decision-id", required=True)
    qualified_shadow_parser.add_argument("--product", required=True)
    qualified_shadow_parser.add_argument("--observed-at", type=int, required=True)
    qualified_shadow_parser.add_argument("--actionable-at", type=int, required=True)
    qualified_shadow_parser.add_argument("--target-position", type=int, choices=(-1, 0, 1), required=True)
    qualified_shadow_parser.add_argument("--strategy-id", required=True)
    qualified_shadow_parser.add_argument("--rationale-code", required=True)
    qualified_shadow_parser.add_argument("--signal-candle-timestamp", type=int)
    qualified_shadow_parser.add_argument("--observation-id", action="append", required=True)
    qualified_shadow_parser.add_argument("--max-event-age-seconds", type=int, required=True)
    qualified_shadow_parser.add_argument("--max-transport-age-seconds", type=int, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser(); args = parser.parse_args(argv); root = args.root.resolve()
    try:
        if args.command == "validate":
            checks = validate(root); checks.extend(_validate_learning_state_or_raise(root)); _print({"status": "ok", "root": str(root), "checks": checks})
        elif args.command == "status":
            _validate_learning_state_or_raise(root); _print(status_summary(root))
        elif args.command == "context_status":
            _print(market_context_status(root))
        elif args.command == "context_materialize":
            context, receipt = materialize_market_context(root, cutoff_at_ns=args.cutoff_at_ns)
            _print({"status": "ok", "context_id": context.context_id, "context_content_hash": context.content_hash(), "cutoff_at_ns": context.cutoff_at_ns, "known_at_ns": context.known_at_ns, "context_status": context.status, "source_frame_count": len(context.source_frame_ids), "source_set_hash": context.source_set_hash(), "materialization_policy_id": receipt["policy_id"], "materialization_receipt_hash": receipt["integrity"]["content_hash"]})
        elif args.command == "context_profile_register":
            profile_path = args.profile.resolve()
            value = json.loads(profile_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("context profile JSON must be an object")
            profile = profile_from_mapping(value)
            artifact = ModelContextProfileRegistry(root).register(profile, registered_at_ns=args.registered_at_ns, evidence_refs=args.evidence)
            _print({"status": "ok", "profile_id": artifact["profile_id"], "model_ref": profile.model_ref, "profile_version": profile.profile_version, "profile_hash": profile.content_hash(), "artifact_content_hash": artifact["integrity"]["content_hash"]})
        elif args.command == "next-work": _print({"status": "ok", "next_work": next_work(root)})
        elif args.command == "recover":
            result = recover_pending(root); _validate_learning_state_or_raise(root); _print(result)
        elif args.command == "monitor_snapshot": _print(monitor_snapshot(root))
        elif args.command == "predecessor_verify": _print(verify_manifest(args.manifest.resolve(), args.source_root.resolve()))
        elif args.command == "transition": _print({"status": "ok", "transition": transition(args.new_state, args.trigger, args.decision_id, args.evidence, root)})
        elif args.command == "task-status":
            task, candidate = update_task(args.task_id, args.status, root); _print({"status": "ok", "task": task, "next_work": candidate})
        elif args.command == "qualified_shadow_record":
            proposal = ShadowDecisionProposal(decision_id=args.decision_id, product=args.product, observed_at=args.observed_at, actionable_at=args.actionable_at, target_position=args.target_position, strategy_id=args.strategy_id, rationale_code=args.rationale_code, signal_candle_timestamp=args.signal_candle_timestamp)
            decision = record_qualified_shadow_decision(root, proposal, args.observation_id, max_event_age_seconds=args.max_event_age_seconds, max_transport_age_seconds=args.max_transport_age_seconds); _print({"status": "ok", "decision": decision})
        else: parser.error("unknown command: %s" % args.command)
    except StateValidationError as exc: _print({"status": "invalid", "errors": exc.errors}); return 2
    except (KeyError, RuntimeError, ValueError, json.JSONDecodeError, OSError, PredecessorVerificationError) as exc: _print({"status": "error", "error": str(exc)}); return 2
    return 0


if __name__ == "__main__": sys.exit(main())
