# Phase 12 — Live Readiness Assessment (No Automatic Activation)

## Goal
Produce an independent, documented go/no-go decision after strategy research, backtesting, paper-execution validation, AI-agent controls, forward paper trading, and deployment/reliability testing. This task does not authorise live trading.

## Required reviews
- [ ] Independent quantitative review of out-of-sample backtest and forward paper-trading results.
- [ ] Confirm 30–60 trading days of paper observation and document missing sessions, sample size, and regime limitations.
- [ ] Review net performance after realistic costs, drawdown, risk-adjusted metrics, benchmark comparison, and sensitivity to slippage/fill assumptions.
- [ ] Review whether any apparent performance depends on a small number of trades, instruments, or market conditions.
- [ ] Review strategy version control and confirm no silent changes occurred during experiments.
- [ ] Review AI agent permissions, provider outage behaviour, audit logs, and inability to bypass risk controls.
- [ ] Review cloud deployment, monitoring, backup/restore, incident response, and recovery test evidence.
- [ ] Check current SEBI, exchange, broker, and other applicable requirements with authoritative sources or qualified professionals.
- [ ] Check current Zerodha API terms, authentication, static IP requirements, rate limits, product availability, and operational constraints.
- [ ] Verify market-data use rights, licensing, entitlements, and retention rules.
- [ ] Review secret management, host security, network access, and credential rotation.
- [ ] Test idempotency, duplicate-order prevention, broker-position reconciliation design, kill switch, and fail-closed behaviour in a non-live test environment.
- [ ] Define human supervision, incident response, escalation, and manual shutdown procedures.
- [ ] Disclose expected infrastructure/API/brokerage costs and capital at risk in plain language.
- [ ] Define restricted instruments, position limits, daily loss limits, and staged capital limits for any separately proposed live implementation.
- [ ] Obtain explicit user approval for a separate live-trading implementation milestone.
- [ ] Independently verify that all evidence used in the assessment is reproducible, traceable, and based on permitted data.
- [ ] Review the completeness and effectiveness of the paper-trading risk controls, including loss limits, exposure limits, kill switch, and reconciliation.
- [ ] Review operational ownership, support arrangements, and the consequences of unattended failures.
- [ ] Confirm that all material findings, unresolved risks, assumptions, and accepted limitations are documented.
- [ ] Verify that the proposed live implementation has a separate rollback and emergency-disable plan.

## Default decision
**NO-GO** unless every applicable requirement is satisfied and independently reviewed.

Passing this assessment does not guarantee profitability or authorise automatic activation. Any live broker integration and live execution must be implemented in a separate, explicitly approved milestone with its own acceptance tests. No AI agent, deployment pipeline, or configuration change may activate live trading.

## Acceptance criteria
- The assessment report identifies each requirement as passed, failed, not applicable, or unresolved, with supporting evidence.
- Any unresolved critical safety, regulatory, data-licensing, or financial risk results in NO-GO.
- Approval applies only to the reviewed strategy, instruments, limits, infrastructure, broker integration, and software release.
- Any material change after approval requires reassessment before live activation.
- Live execution remains disabled until the separate implementation milestone passes its own acceptance tests and receives explicit user authorization.