# Trader Terminal V2.3 audit — Setup Scanner

Date: 2026-08-14

## Outcome

Setup Scanner now asks what research job the trader wants before exposing engine controls. Six
presets cover intraday structure, weekly structure, momentum, mean reversion, liquid-index
momentum, and exposure zones. Advanced engine and expiration controls remain available under
progressive disclosure.

Results are no longer an unstructured wall of cards. The normal scanner opens into a compact
comparison with rank, ticker, bias, structural score, evidence confidence, coverage, and path.
Each row expands into the existing detailed read and exact OPRA coverage/geometry evidence.

## Semantics audit

- Ranking eligibility is evaluated before structural score.
- Minimum coverage is 8 calculated cells and 20 option contracts; missing counts are unknown,
  not zero.
- Spot, directional state, valid target/invalidation geometry, and OPRA feed are explicit gates
  for directional strategies.
- Cluster scans retain their distinct structural semantics and do not invent invalidation.
- `higher`, `developing`, and `insufficient` are evidence-quality labels, never probabilities.
- Every rejection records one or more machine-readable reasons and the API returns counts plus
  bounded examples.
- Actual worker concurrency is now reported rather than hardcoded to one.
- Unit-test scans and scheduled Research Desk scans no longer pollute the user's scanner history.
- Legacy rows with missing optional numeric fields render as unavailable instead of crashing.

## Live verification

A seven-symbol OPRA scan of MU, SNDK, NVDA, AAPL, SPY, QQQ, and IWM completed in 8.4 seconds:

- scanned: 7
- qualified/ranked: 5
- rejected: 2
- provider errors: 0
- actionable: 5

The two rejected symbols reported direction, geometry, coverage, score, and structure blockers
rather than silently disappearing. All five displayed rows had sufficient coverage; four were
labelled `higher` and MU was `developing`.

## Verification

- Scanner safety/gating tests: 5 passed.
- Full Python suite after scanner changes: 877 passed, 2 skipped.
- Web typecheck and lint: pass.
- Web source tests: 46 passed.
- Dependency audit: zero vulnerabilities.
- Production build, atomic publication, and service restart: pass.
- Authenticated Chromium: 5/5 desktop/mobile/product journeys passed.
- Desktop and mobile scanner screenshots were captured; the desktop comparison was visually
  reviewed at `web/test-results/setup-scanner-v2-desktop.png`.

## Residual gaps / next phase

- Event-risk cannot be a truthful preset until authoritative earnings/action data is connected;
  it was intentionally not fabricated.
- The specialist cluster and Flash/Agentic views still use richer cards because their schemas
  are not directly comparable to normal directional rows.
- Saved-scan comparison across two dates is not yet a first-class diff; history remains load-one.
- Night Vision remains the largest visual/interaction gap and is the next phase.
