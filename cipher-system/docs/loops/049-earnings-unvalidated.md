# Loop 049 plan — Earnings `UNVALIDATED_FOR_LIVE_OPTIONS_PNL`

**Goal:** Earnings Radar (API, live panel, guest fallback) shows the gate token `UNVALIDATED_FOR_LIVE_OPTIONS_PNL`. Paper P&L stays estimated. Missing stays unknown.

**Why this loop exists:** PROGRAM 049. The radar converts `strategy_gate` with `replaceAll("_", " ")`, so the token never appears. Core caveat says “not live option P&L” without the gate id. Guest fallback has no token.

**Architecture:**
- Core `earnings_radar()` caveat includes the token on current and unavailable payloads.
- UI prints `strategy_gate` verbatim (fallback to the token).
- Paper scorecard and guest Earnings Radar copy include the token.

**Files:**
- Modify: `core/app.py`, `tests/test_earnings_radar_endpoint.py`, EarningsRadar.tsx, GuestShowcase.tsx
- Create: `web/test/earnings-unvalidated.test.mjs`
- Create: `gates/loop-049-earnings-unvalidated.md`

**Preserve:** Estimated win/P&L labels, `{data.caveat}` still rendered, overflow both axes, no quote retrofit, no order surface.

**Anti-goals:** Do not capture live option marks (loop 050). No commit. No hosted E2E (copy-only).

**Checks:** earnings-unvalidated + earnings-radar-ui; python earnings radar endpoint; full web Node; lint; typecheck; sync.
