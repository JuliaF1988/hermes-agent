"""Wall-clock run-budget helpers shared by loop and provider wait paths."""

from __future__ import annotations

import time
from typing import Any


class RunBudgetExceeded(TimeoutError):
    """A provider request outlived the conversation turn's wall-clock budget."""


class ProviderStaleTimeout(TimeoutError):
    """A bounded run stopped after one provider no-progress window."""


class FinalSynthesisTimeout(TimeoutError):
    """A tool-free final response exceeded its absolute synthesis deadline."""


def remaining_run_budget_seconds(agent: Any, *, now: float | None = None) -> float | None:
    """Remaining seconds, or ``None`` when this turn has no active wall-clock budget."""
    budget = getattr(agent, "run_budget_seconds", None)
    started = getattr(agent, "_run_budget_started_at", None)
    if not isinstance(budget, (int, float)) or isinstance(budget, bool) or budget <= 0 or not started:
        return None
    return float(budget) - ((time.time() if now is None else now) - float(started))


def cap_timeout_to_run_budget(agent: Any, timeout: float) -> float:
    """Never let one provider wait extend beyond an active run budget."""
    remaining = remaining_run_budget_seconds(agent)
    if remaining is None:
        return timeout
    return max(0.05, min(float(timeout), remaining))


def arm_final_synthesis_deadline(agent: Any, *, now: float | None = None) -> float | None:
    """Arm the tool-free final deadline once, ideally when its last tool result lands.

    Provider stale timeouts are progress-aware: reasoning chunks legitimately refresh
    them. A final synthesis needs a different guarantee because an endlessly reasoning
    model can otherwise keep the stream alive without producing a visible answer. The
    explicit provider stale setting is reused as the absolute final-turn budget, with
    the conversation run budget remaining the outer cap.
    """
    existing = getattr(agent, "_final_synthesis_deadline", None)
    if isinstance(existing, (int, float)):
        return float(existing)
    try:
        timeout, _implicit = agent._resolved_api_call_stale_timeout_base()
        timeout = float(timeout)
    except Exception:
        return None
    if timeout <= 0:
        return None
    current = time.time() if now is None else float(now)
    remaining = remaining_run_budget_seconds(agent, now=current)
    if remaining is not None:
        timeout = min(timeout, max(0.05, remaining))
    deadline = current + timeout
    agent._final_synthesis_deadline = deadline
    return deadline


def remaining_final_synthesis_seconds(agent: Any, *, now: float | None = None) -> float | None:
    """Seconds left for an armed forced-final turn, or ``None`` when unarmed."""
    deadline = getattr(agent, "_final_synthesis_deadline", None)
    if not isinstance(deadline, (int, float)):
        return None
    return float(deadline) - (time.time() if now is None else float(now))
