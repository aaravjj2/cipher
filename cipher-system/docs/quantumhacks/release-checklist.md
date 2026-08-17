# QuantumHacks release checklist

## Eligibility and Devpost

- [ ] Confirm every listed team member is a student and above the local age of
  majority.
- [ ] Register/join the official QuantumHacks Devpost before submission.
- [ ] Confirm the final deadline displayed by Devpost is August 20, 2026 at
  8:00 PM EDT.
- [ ] Add project name, team, description, technologies, impact, and installation.
- [ ] Supply a public **sanitized** GitHub repository.
- [ ] Upload a 2–5 minute video and representative screenshots.
- [ ] Do not include `.env`, credentials, local databases, captured emails,
  browser profiles, private GEX/option archives, or vendor/commercial material.

## Product freeze

- [ ] Freeze the demo commit and record its SHA.
- [ ] Run all Python tests, Node checks, typecheck, lint, production build, and
  authenticated Playwright journeys.
- [ ] Run a secret scan and inspect every public-repository file.
- [ ] Run the read-only safety scan for broker clients and order endpoints.
- [ ] Verify core/web health, Alpaca feeds, paper executor reconciliation, latest
  data timestamps, and no worker exception.
- [ ] Verify marked equity, liquidation equity, risk locks, decision traces, and
  cohort sample warnings render correctly.
- [ ] Verify a frozen scanner candidate replays identically in Night Vision.
- [ ] Verify the app remains usable at 390 px width and by keyboard.

## Demo capture

- [ ] Use a clean browser profile and hide bookmarks, email, tokens, local paths,
  and notifications.
- [ ] Record at 1080p with readable zoom and a 3:30 target duration.
- [ ] Start from a deterministic saved workspace.
- [ ] Show one successful workflow and one honest fail-closed/missing-data state.
- [ ] Show the paper-only boundary and GEX caveat on screen.
- [ ] Export 5–7 screenshots: Today, Scanner, Night Vision, Options Terminal,
  Paper Portfolios, Research Desk, architecture.

## Final review

- [ ] Every quantitative claim in the description matches the frozen build.
- [ ] No tiny-sample strategy is called validated, best, profitable, or proven.
- [ ] The video, repository, and description use the same product name and pitch.
- [ ] Test installation from a clean checkout.
- [ ] Submit at least six hours before the deadline and verify the project page
  in a signed-out browser.
