---
name: trading-team
description: >
  Audit, diagnostic, validation and improvement planning for the Titanium V14
  algorithmic trading codebase. Use for V14 strategy, signal, risk, execution,
  profitability, walk-forward or trading-code review requests.
---

# Titanium V14 trading team

## Objective

Coordinate evidence-based analysis of V14 without granting any LLM execution
authority. The deterministic gates and the fail-closed RiskGate remain sovereign.

## Mandatory safety

- PAPER/DEMO only. Never place a real order.
- Do not edit `.env`, arm execution, restart a service or approve a permission
  without Florent's explicit instruction for that exact action.
- Never expose secrets in reports, logs or the collaboration bus.
- A skill is a workflow guide, not a permission grant.
- Strategy changes require tests and out-of-sample evidence after broker costs.

## V14 analysis sequence

1. Map the relevant path in `titanium/`, `tradingagents/`, `tools/` and `tests/`.
2. Trace data -> features -> deterministic gates -> LLM deliberation -> RiskGate
   -> execution policy.
3. Audit parameters, broker costs, symbol normalization and context keys.
4. Review portfolio exposure, correlated clusters, sizing and fail-closed behavior.
5. Verify journal integrity, reconciliation, MAE/MFE and performance attribution.
6. Evaluate sample size, expectancy, profit factor, drawdown and walk-forward/OOS
   robustness. Never call a configuration profitable from in-sample results alone.
7. Propose the smallest reversible change, implement only when requested, and run
   tests proportional to risk.
8. Publish a result-first report with evidence, remaining risks and a clear verdict.

## Roles

- Claude: principal implementer and heavy local runs when the environment supports it.
- Codex: independent audit, red-team review, targeted fixes and test verification.
- Hermes: coordination, memory and non-executable analysis.
- Florent: final authority for sensitive actions and promotion decisions.

## Canonical V14 outputs

- Current state and decisions: local `collab/` documents.
- Agent exchanges: common collaboration bus described in
  `collab/HERMES_BRIDGE.md`.
- Test evidence: exact command, result, date and affected files.
- Performance verdict: net of spread, commission, slippage and swap, with sample
  size and OOS status stated explicitly.

## Promotion gate

No promotion recommendation unless all applicable checks pass: positive net
expectancy, acceptable profit factor and drawdown, reconciled new closures,
sufficient clean samples, walk-forward/OOS robustness and Florent's explicit
human approval.
