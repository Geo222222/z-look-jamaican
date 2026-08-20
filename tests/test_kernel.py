import os
from pathlib import Path
import pytest

os.environ["ZLOOK_DATABASE_URL"] = "sqlite:////tmp/zlook-test.db"
os.environ["ZLOOK_REFLECTION_EVERY_CYCLES"] = "2"
Path("/tmp/zlook-test.db").unlink(missing_ok=True)

from app.kernel import AutonomousKernel, GovernorPolicy, GovernorViolation, init_db


def test_zero_revenue_blocks_live_trade():
    with pytest.raises(GovernorViolation):
        GovernorPolicy().assert_trade_allowed(retained_realized_revenue_usd=0, trade_notional_usd=.01, concurrent_exposure_usd=0, daily_realized_loss_usd=0)


def test_earned_capital_limits():
    limits = GovernorPolicy().limits(1000)
    assert limits == {"max_total_financial_allocation_usd": 200, "max_single_trade_usd": 5, "max_concurrent_exposure_usd": 50, "max_daily_realized_loss_usd": 10}


def test_bootstrap_and_cycle_are_durable():
    init_db()
    kernel = AutonomousKernel()
    kernel.bootstrap()
    state = kernel.snapshot()
    assert state["kernel"]["external_capital_usd"] == 0
    assert state["kernel"]["state"] == "DISCOVERY"
    assert state["top_opportunity"] is not None
    assert state["top_opportunity"]["live_validated"] is False
    result = kernel.run_cycle()
    assert result.cycle == 1
    assert result.top_opportunity_id
