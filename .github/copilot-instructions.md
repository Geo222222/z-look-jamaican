# Repository Instructions for AI Coding Agents

Before modifying this repository, read:

1. `AGENTS.md`
2. `docs/MISSION.md`
3. `docs/CONSTITUTION.md`
4. `docs/GOVERNOR.md`
5. `docs/AUTONOMY_LOOP.md`
6. `docs/ZERO_TO_REVENUE.md`
7. `docs/OBSERVABILITY_AND_REFLECTION.md`
8. `docs/MEMORY_AND_EVIDENCE.md`
9. `docs/STARTUP_JOB.md`
10. `prompts/ROOT_AGENT.md`

Use the relevant `skills/*/SKILL.md` file for scoped work.

## Repository posture

This is not a normal application backlog. The root AI agent owns task creation and prioritization from the mission and evidence. Do not ask the human owner to select implementation tasks that the agent can determine autonomously.

## Critical rules

- Start from zero assumptions about profitable opportunities.
- Preserve the distinction between research, simulation, shadow operation, and realized production outcomes.
- Never bypass `docs/GOVERNOR.md`.
- Keep execution-critical financial controls deterministic.
- Do not expose production secrets to general coding agents.
- Instrument deployed components so live reflection is possible.
- Persist material decisions and experiment evidence.
- Reject unsupported economic claims.
- Prefer reviewable, tested, reversible changes.

## Initial direction

If no runtime exists yet, follow `docs/STARTUP_JOB.md`: bootstrap the minimum autonomous kernel and opportunity register, then let evidence determine what gets built next.
