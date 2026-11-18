# Project Aegis — Open Decisions and Deferred Scope

This register is for unresolved questions and prerequisite gates. **Nothing in this
document is approved merely because an option is listed.** Suggested options are
proposals only. Do not implement an open item without explicit user approval and, where
appropriate, an update to its controlling task specification.

| ID | Status | Open matter / dependency | Evidence and consequence |
|---|---|---|---|
| OPEN-01 | REQUIRES USER/ADMIN ACTION | Verify complete official calendar coverage for the intended exchange/segment/date range, including the 2026 cash-market CAS limitations, 2027, and other instruments. | Current checked-in NSE candidate is `CONFIGURED`, not operationally `CONFIRMED`; calendar-dependent completeness gates remain fail-closed. See [`EXCHANGE_CALENDAR_CONFIGURATION.md`](./EXCHANGE_CALENDAR_CONFIGURATION.md) and its evidence file. |
| OPEN-02 | REQUIRES USER/LEGAL VERIFICATION | Identify historical/live data sources and verify permitted use, licensing, entitlements, retention, and any redistribution constraints. | `VERIFIED_PERMITTED` is a recorded declaration, not legal verification. No source permission is inferred from tests or metadata. |
| OPEN-03 | CLOSED — 2026-11-16 | Independently review and accept the M2-B coordinator before M2-C. | The independent read-only audit returned ACCEPT on 2026-11-16 for M2-B shared qualification only. This is owner-reported history; the original artifact is absent. The subsequent schema-provenance correction still needs focused independent review, not automatic reacceptance. Separately, M2-C has independent bounded ACCEPT for qualified historical per-sleeve M1B composition, according to project-owner-supplied acceptance history. That decision does not close the M2-B correction caveat. See `PROJECT_STATE.md` for evidence limitations. |
| OPEN-04 | OPEN — M2 policy | Whether confirmed-holiday stale marks need an additional age/session cap, beyond the approved rule allowing carry only on calendar-confirmed non-trading days. | The M2 specification identifies no approved maximum carry age. Adding a cap would change policy. |
| OPEN-05 | OPEN — M2 API | Final public exception type and whether blocked qualification reports the first stable reason or accumulates all deterministic reasons. | The M2 specification leaves this open. Either implementation must fail closed; do not choose silently. |
| OPEN-06 | OPEN — future M2 version | Whether future versions support scale-ins, partial fills, shorts, leverage, derivatives, BSE, adjusted data, or cross-currency accounting. | Explicitly out of M2 v1. No current capability or design is approved. |
| OPEN-07 | OPEN — AI research protocol | Numerical minimum samples, confidence levels, statistical/multiple-testing methods, cost-stress multiplier, performance/drawdown/promotion thresholds, the meaning of “material” validation, and stopping rules. | The 1.5x–2x cost-stress range is a proposed evaluation example only, not an approved requirement or universal acceptance threshold. Approve and pre-register selected methods and pass/fail criteria before evaluation. |
| OPEN-08 | OPEN — AI research protocol | Exact model/prompt/tool version freezing, external response retention, privacy, and deterministic replay policy for AI-1C. | Existing security documentation avoids storing raw prompts/responses in some paths; do not weaken it. Define a compatible, approved record format before AI-1C acceptance. |
| OPEN-09 | BLOCKING PREREQUISITE | Establish a permitted, quality-gated real historical dataset and an untouched out-of-sample evaluation plan for Phase 4/7 and AI research claims. | Synthetic tests do not establish real-data validity, predictive edge, or cost-aware performance. |
| OPEN-10 | PLANNED / PREREQUISITE-GATED | Define and authorize Phase 10's live-data provider, operational calendar, observation-quality thresholds, and 30–60 actual-session evidence period. | Phase 10 remains planned; synthetic forward-runtime work does not equal live-data forward-paper readiness or authorization. |
| OPEN-11 | FUTURE / REQUIRES APPROVAL | Cloud deployment requirements, budget, operator model, recovery objectives, and security acceptance for Phase 11. | No hosting or paid service is authorized by a roadmap entry. |
| OPEN-12 | FUTURE / DEFAULT NO-GO | Whether to conduct a separate live-readiness assessment and, later, whether a distinct live implementation should even be proposed. | Phase 12 is an assessment, not authorization. Any unresolved critical issue means NO-GO; explicit approval and a separate implementation gate are required. |
| OPEN-13 | REQUIRES USER AUTHORIZATION | Safe, scoped inspection or migration of the normal application database, if ever needed. | Current on-disk contents/state are not asserted here. A historical Phase 9 report records an unexpected migration; preserve current data and do not recover or alter it without authorization. |
| OPEN-14 | PENDING — distinct-reviewer independent sign-off | Independently review the implemented, technically ACCEPTED M2-D accounting. | Technical ACCEPT is recorded from project-owner-supplied history and is not independent acceptance. M2-C is independently accepted within its bounded scope. M2-E has not started and is not authorized; overall M2 is not accepted. No forward-paper operational readiness or live-trading authorization is established. See `PROJECT_STATE.md` for evidence limitations. |

## Proposed options (not decisions)

The [next-session handoff](./PROJECT_HANDOFF_2026-11-18.md) adds the following pending
gates without closing existing decisions or changing approved architecture:

| ID | Status | Open matter / dependency | Evidence and consequence |
|---|---|---|---|
| OPEN-15 | PENDING — independent acceptance | Narrow Checkpoint 2 review of approved-calendar provenance and the original Phase 8 public result identity. | Blockers are reported corrected; 34 new regressions and 305 focused tests are implementer-reported passes. Checkpoint 2 is not independently accepted or committed. |
| OPEN-16 | PENDING APPROVAL — journal retention | Approve exact retention, permitted evidence storage, replay inputs, backup and export rules for [Decision Journal v1](../tasks/DECISION_JOURNAL_V1.md). | Journal implementation is planned; no schema migration or normal database access is authorized. Preserve OPEN-02 source rights and OPEN-08 privacy restrictions. |
| OPEN-17 | PENDING APPROVAL — bounded pilot and mode | Authorize any future [live-data paper MVP](../tasks/PHASE_10_LIVE_DATA_PAPER_MVP.md), its instrument, simulated risk policy and operating mode separately. | `RESEARCH_ONLY` remains mandatory. Proposed `LIVE_DATA_PAPER` is unavailable and unauthorized; the SMA-first MVP does not replace full Phase 10's AI, multiple-strategy, fill-model or observation requirements. API entitlement, subscription and connection need explicit approval under OPEN-02; instrument/session/calendar approval remains required under OPEN-01/10. |
| OPEN-18 | OPEN — observed-candle quality | Approve fidelity, gap, freshness and coverage thresholds for stream-observed candles before any operational eligibility. | Stream observations do not prove exchange-complete OHLCV. Measure first, then approve criteria before use; retain OPEN-07 research thresholds and existing full Phase 10 acceptance requirements. |

- For OPEN-05, possible API choices are to return all deterministic block reasons in
  frozen-universe order or to return the first deterministic reason only. The choice
  remains open.
- For OPEN-04, a future policy could cap carry by confirmed sessions or dates, or retain
  the M2 v1 non-trading-day-only rule. No cap is authorized by this list.
- For OPEN-07, candidate evaluation may choose robust cost stresses, confidence methods,
  and sample requirements appropriate to data and instrument. The 1.5x–2x stress range
  is only a proposal; no numeric value or method here is an approved pass/fail criterion.
