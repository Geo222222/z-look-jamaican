from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZLOOK_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./state/zlook.db"
    poll_interval_seconds: int = 30
    reflection_every_cycles: int = 10
    log_level: str = "INFO"

    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parents[1]


settings = Settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(100), index=True)
    mechanism: Mapped[str] = mapped_column(Text)
    payer: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(32), default="DISCOVERY", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    capital_required_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expected_time_to_falsify_hours: Mapped[float] = mapped_column(Float, default=24.0)
    next_experiment: Mapped[str] = mapped_column(Text, default="")
    rejection_criteria: Mapped[str] = mapped_column(Text, default="")
    reopening_criteria: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    hypothesis: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Objective(Base):
    __tablename__ = "objectives"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(200))
    decision: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reversal_conditions: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reflection(Base):
    __tablename__ = "reflections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle: Mapped[int] = mapped_column(Integer, index=True)
    expected: Mapped[str] = mapped_column(Text)
    observed: Mapped[str] = mapped_column(Text)
    delta: Mapped[str] = mapped_column(Text)
    lesson: Mapped[str] = mapped_column(Text)
    next_action: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SpecialistTask(Base):
    __tablename__ = "specialist_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(80), index=True)
    parent_objective_id: Mapped[str] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    priority: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RuntimeState(Base):
    __tablename__ = "runtime_state"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def _prepare_sqlite(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_absolute():
        path = settings.root / path
    path.parent.mkdir(parents=True, exist_ok=True)


_prepare_sqlite(settings.database_url)
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class GovernorViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class GovernorPolicy:
    external_starting_capital_usd: float = 0.0
    manual_human_funding_allowed: bool = False
    borrowing_allowed: bool = False
    leverage_allowed: bool = False
    max_financial_allocation_pct_retained_revenue: float = 0.20
    max_single_trade_pct_retained_revenue: float = 0.005
    max_concurrent_exposure_pct_retained_revenue: float = 0.05
    max_daily_realized_loss_pct_retained_revenue: float = 0.01

    def limits(self, retained_realized_revenue_usd: float) -> dict[str, float]:
        r = max(0.0, retained_realized_revenue_usd)
        return {"max_total_financial_allocation_usd": r * self.max_financial_allocation_pct_retained_revenue, "max_single_trade_usd": r * self.max_single_trade_pct_retained_revenue, "max_concurrent_exposure_usd": r * self.max_concurrent_exposure_pct_retained_revenue, "max_daily_realized_loss_usd": r * self.max_daily_realized_loss_pct_retained_revenue}

    def assert_trade_allowed(self, *, retained_realized_revenue_usd: float, trade_notional_usd: float, concurrent_exposure_usd: float, daily_realized_loss_usd: float) -> None:
        limits = self.limits(retained_realized_revenue_usd)
        if retained_realized_revenue_usd <= 0:
            raise GovernorViolation("No retained realized revenue is available as autonomous risk capital")
        if trade_notional_usd > limits["max_single_trade_usd"]:
            raise GovernorViolation("Trade exceeds deterministic single-trade limit")
        if concurrent_exposure_usd + trade_notional_usd > limits["max_concurrent_exposure_usd"]:
            raise GovernorViolation("Trade exceeds deterministic concurrent-exposure limit")
        if daily_realized_loss_usd >= limits["max_daily_realized_loss_usd"]:
            raise GovernorViolation("Daily realized-loss circuit breaker is active")


POLICY = GovernorPolicy()


@dataclass(frozen=True)
class Candidate:
    slug: str
    title: str
    domain: str
    mechanism: str
    payer: str
    next_experiment: str
    score: float
    confidence: float
    capital_required_usd: float
    time_to_falsify_hours: float


SEED_CANDIDATES = [
    Candidate("protocol-data-api", "Protocol data and anomaly API", "protocol_data", "Normalize public protocol state into a dependable developer API.", "Developers/autonomous systems paying for normalized data or alerts.", "Identify one high-friction protocol data workflow and compare freshness/reliability of substitutes.", 2.91, .35, 5, 25),
    Candidate("developer-micro-api", "Autonomous developer micro-API", "developer_api", "Solve one repetitive developer task through a metered API.", "Developers paying per request or subscription.", "Mine public pain signals and benchmark one reproducible problem against existing substitutes.", 2.64, .30, 2, 29),
    Candidate("keeper-automation", "Protocol keeper/automation service", "defi_infrastructure", "Perform permissionless maintenance where protocols expose economic incentives.", "Protocol incentives or users paying for reliable automation.", "Survey keeper interfaces and replay reward minus gas/failure/competition on public events.", 1.86, .30, 20, 25),
    Candidate("same-chain-dex-arb", "Same-chain DEX execution inefficiency", "defi", "Detect and atomically execute same-chain routing/price discrepancies.", "On-chain market inefficiency captured through atomic execution.", "Collect quotes and replay gas, fees, slippage, ordering and failure assumptions without capital.", 1.20, .25, 45, 18),
]


@dataclass
class CycleResult:
    cycle: int
    state: str
    action: str
    top_opportunity_id: str | None


class AutonomousKernel:
    def bootstrap(self) -> None:
        with session_scope() as s:
            state = s.get(RuntimeState, "kernel")
            if state is None:
                state = RuntimeState(key="kernel", value={"state": "BOOTSTRAP", "cycle": 0, "external_capital_usd": 0.0, "retained_realized_revenue_usd": 0.0, "last_action": "initialize durable state"})
                s.add(state)
            if s.get(Objective, "OBJ-MISSION") is None:
                s.add(Objective(id="OBJ-MISSION", parent_id=None, kind="MISSION", title="Build sustainable autonomous realized USD economic value from zero", rationale="Repository mission", priority=100.0))
            for c in SEED_CANDIDATES:
                oid = f"OPP-{c.slug.upper()}"
                if s.get(Opportunity, oid) is None:
                    s.add(Opportunity(id=oid, title=c.title, domain=c.domain, mechanism=c.mechanism, payer=c.payer, score=c.score, confidence=c.confidence, capital_required_usd=c.capital_required_usd, expected_time_to_falsify_hours=c.time_to_falsify_hours, next_experiment=c.next_experiment, rejection_criteria="Reject if realistic net economics or autonomous operability are non-viable.", reopening_criteria="Reopen only on materially changed economics, technology, demand, or competition.", evidence={"source": "bootstrap hypothesis universe", "live_validated": False}))
            if s.scalar(select(Experiment).limit(1)) is None:
                top = s.scalar(select(Opportunity).order_by(Opportunity.score.desc()).limit(1))
                if top:
                    s.add(Experiment(id=f"EXP-{uuid4().hex[:12].upper()}", opportunity_id=top.id, hypothesis=f"{top.title} deserves a deeper falsification experiment.", method=top.next_experiment, success_criteria="Current evidence materially raises or lowers confidence and quantifies economics."))
            self._ensure_research_task(s)
            if s.get(Decision, "DEC-BOOTSTRAP-001") is None:
                s.add(Decision(id="DEC-BOOTSTRAP-001", subject="Initial architecture", decision="Use a small persistent kernel with deterministic Governor enforcement and hypothesis-first discovery.", rationale="Minimizes complexity while enabling autonomous continuation.", evidence={"external_starting_capital_usd": 0}, reversal_conditions="Replace components only when measured bottlenecks justify more complexity."))
            state.value = {**state.value, "state": "DISCOVERY", "last_action": "bootstrap complete"}

    def _ensure_research_task(self, s: Session) -> None:
        active = s.scalar(select(SpecialistTask).where(SpecialistTask.status.in_(["PENDING", "RUNNING"])).limit(1))
        if active is not None:
            return
        top = s.scalar(select(Opportunity).where(Opportunity.stage.notin_(["REJECTED", "SUSPENDED"])).order_by(Opportunity.score.desc()).limit(1))
        if top is None:
            return
        s.add(SpecialistTask(id=f"TASK-{uuid4().hex[:12].upper()}", role="Opportunity Researcher", parent_objective_id="OBJ-MISSION", opportunity_id=top.id, title=f"Falsify or strengthen {top.id}", instruction=f"Research current primary-source evidence for {top.title}. Mechanism: {top.mechanism} Payer/value source: {top.payer} Next experiment: {top.next_experiment} Return evidence, economics, major risks, confidence_delta from -0.5 to 0.5, and a concrete next experiment. Do not claim profitability from visible spread, simulated P&L, or marketing material.", priority=top.score))

    def claim_task(self, worker_id: str, lease_minutes: int = 30) -> dict[str, Any] | None:
        with session_scope() as s:
            now = utcnow()
            task = s.scalar(select(SpecialistTask).where((SpecialistTask.status == "PENDING") | ((SpecialistTask.status == "RUNNING") & (SpecialistTask.lease_until < now))).order_by(SpecialistTask.priority.desc(), SpecialistTask.created_at.asc()).limit(1))
            if task is None:
                return None
            task.status = "RUNNING"
            task.worker_id = worker_id
            task.lease_until = now + timedelta(minutes=max(1, lease_minutes))
            return {"id": task.id, "role": task.role, "title": task.title, "instruction": task.instruction, "opportunity_id": task.opportunity_id, "lease_until": task.lease_until.isoformat()}

    def complete_task(self, task_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        with session_scope() as s:
            task = s.get(SpecialistTask, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.worker_id not in (None, worker_id):
                raise PermissionError("Task is leased to another worker")
            task.status = "COMPLETED"
            task.worker_id = worker_id
            task.lease_until = None
            task.result = result
            if task.opportunity_id:
                opp = s.get(Opportunity, task.opportunity_id)
                if opp is not None:
                    delta = max(-0.5, min(0.5, float(result.get("confidence_delta", 0.0))))
                    opp.confidence = max(0.0, min(1.0, opp.confidence + delta))
                    opp.stage = "RESEARCH"
                    opp.evidence = {**(opp.evidence or {}), "latest_task_id": task.id, "latest_result": result}
                    next_exp = result.get("next_experiment")
                    if isinstance(next_exp, str) and next_exp.strip():
                        opp.next_experiment = next_exp.strip()
                    if result.get("recommendation") == "REJECT":
                        opp.stage = "REJECTED"
            return {"id": task.id, "status": task.status, "opportunity_id": task.opportunity_id}

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with session_scope() as s:
            tasks = list(s.scalars(select(SpecialistTask).order_by(SpecialistTask.created_at.desc()).limit(limit)))
            return [{"id": t.id, "role": t.role, "title": t.title, "status": t.status, "opportunity_id": t.opportunity_id, "worker_id": t.worker_id} for t in tasks]

    def run_cycle(self) -> CycleResult:
        with session_scope() as s:
            state = s.get(RuntimeState, "kernel")
            if state is None:
                raise RuntimeError("Kernel is not bootstrapped")
            cycle = int(state.value.get("cycle", 0)) + 1
            top = s.scalar(select(Opportunity).where(Opportunity.stage.notin_(["REJECTED", "SUSPENDED"])).order_by(Opportunity.score.desc(), Opportunity.confidence.desc()).limit(1))
            action = "maintain discovery" if top is None else f"advance falsification for {top.id}: {top.next_experiment}"
            value = dict(state.value)
            value.update({"cycle": cycle, "state": "RESEARCH", "last_action": action, "top_opportunity_id": top.id if top else None})
            state.value = value
            self._ensure_research_task(s)
            if cycle % max(1, settings.reflection_every_cycles) == 0:
                s.add(Reflection(id=f"REF-{uuid4().hex[:12].upper()}", cycle=cycle, expected="The highest-ranked opportunity remains the best next research target unless new evidence changes ranking.", observed=f"Current top opportunity: {top.id if top else 'none'}; action: {action}", delta="No current external evidence adapter is integrated yet.", lesson="Kernel operation is proven; economic truth now requires live evidence and experiment execution.", next_action="Integrate primary-source research and experiment workers without weakening the Governor."))
            return CycleResult(cycle, value["state"], action, value["top_opportunity_id"])

    def snapshot(self) -> dict[str, Any]:
        with session_scope() as s:
            state = s.get(RuntimeState, "kernel")
            top = s.scalar(select(Opportunity).order_by(Opportunity.score.desc()).limit(1))
            experiments = list(s.scalars(select(Experiment).order_by(Experiment.created_at.desc()).limit(10)))
            tasks = list(s.scalars(select(SpecialistTask).order_by(SpecialistTask.created_at.desc()).limit(10)))
            retained = float((state.value if state else {}).get("retained_realized_revenue_usd", 0.0))
            return {"kernel": state.value if state else None, "governor": {"external_starting_capital_usd": 0.0, "manual_human_funding_allowed": False, "borrowing_allowed": False, "leverage_allowed": False, "earned_capital_limits": POLICY.limits(retained)}, "top_opportunity": None if top is None else {"id": top.id, "title": top.title, "score": top.score, "confidence": top.confidence, "stage": top.stage, "live_validated": bool(top.evidence.get("live_validated", False))}, "experiments": [{"id": e.id, "opportunity_id": e.opportunity_id, "status": e.status} for e in experiments], "tasks": [{"id": t.id, "role": t.role, "status": t.status, "opportunity_id": t.opportunity_id} for t in tasks]}
