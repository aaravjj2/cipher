# Trader Terminal V2.2 audit — scheduled Research Desk

Date: 2026-08-14

## Outcome

Cipher now runs a dedicated read-only research synthesis at four weekday market checkpoints:
09:20, 10:15, 12:30, and 15:15 America/New_York. It scans a configurable 26-symbol universe
covering indices, large/liquid names, semiconductors, and tactical liquid names, including MU
and SNDK. The output separates provider/scanner observations from derived ranking labels and
cannot place orders.

## Safety and evidence audit

- Output is explicitly a research candidate, not a recommendation.
- `live_order_authority` is always false; only defined-risk research templates or a wait state
  are emitted.
- Confidence means evidence quality, not predicted win rate.
- Sufficient option coverage and valid target/invalidation geometry gate deeper review.
- Missing coverage is preserved as unknown and forces `insufficient`, never zero-filled.
- Intraday and weekly collection fail independently so a partial provider outage remains visible.
- Source and report timestamps, feed, observed levels, coverage counts, derived thesis, and
  blockers are retained in each record.
- Files are atomically written under `data/research_agent/`; history summaries do not mutate
  prior reports.

## Live verification

- A bounded seven-semiconductor run completed both horizons with 7/7 qualified and no provider
  errors.
- A deployed full run completed all 26 configured symbols in 47 seconds with zero run errors.
- The saved report contains 10 intraday and 10 weekly ranked candidates.
- The systemd service exited successfully and the timer is active; its next activation was
  reported by systemd after installation.
- Core `/api/research-desk` returned the saved 26-symbol report and false live-order authority.
- Authenticated Chromium: 3/3 desktop/mobile/product journeys passed, including switching the
  Research Desk to weekly candidates.
- The Research Desk screenshot was visually reviewed at
  `web/test-results/research-desk-desktop.png`.

The live leaderboard is transient research output and is deliberately not recorded here as a
performance claim. It changes with each capture and requires human review.

## Automated verification

- Research agent/service/research-only tests: 16 passed.
- Full Python suite: 875 passed, 2 skipped.
- Web typecheck and lint: pass.
- Web source tests: 45 passed.
- Production Next.js build and atomic publication: pass; post-publish sync: pass.
- Systemd unit verification: pass.
- Core and proxy services plus research timer: active after deployment.

The legacy `app/public/app.js` check is no longer applicable to the current Next.js static
export because that fixed legacy bundle does not exist; every generated JavaScript artifact
present in `app/public` was syntax-checked instead.

## Residual gaps / next phase

- The scheduler currently uses curated groups in source; user-editable universe controls belong
  in the scanner/research integration phase.
- Ranking still inherits Setup Scanner's older score semantics. V2.3 will add explicit
  pre-score liquidity/data gates, rejection accounting, and purpose-driven presets.
- Weekly output is a structural horizon, not a complete earnings/calendar analysis; authoritative
  event history is scheduled for V2.6.
