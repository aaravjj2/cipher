# Loop ledger

Program: `docs/loops/PROGRAM_2026-08-23.md`
Started: 2026-08-23
Commit policy: do not commit unless the user asks.

| ID | Status | Notes |
|---|---|---|
| 001 | done | Ticker Workbench flatten + guest Overview bound |
| 002 | done | Night Vision chrome; geometry untouched |
| 003 | done | Setup Scanner chrome; scoring unchanged |
| 004 | done | Morning Brief leftover cards |
| 005 | done | Earnings Radar chrome; missing stays unknown |
| 006 | done | Paper Portfolios chrome |
| 007 | done | Autopilot chrome; paper-only |
| 008 | done | Research Desk chrome |
| 009 | done | Portfolio Risk chrome |
| 010 | done | Chart Workbench chrome |
| 011 | done | Company Context chrome |
| 012 | done | Spyglass 8px controls; 10px scrollport kept |
| 013 | done | Holdings chrome |
| 014 | done | Standing chrome |
| 015 | done | Watchlists chrome |
| 016 | done | Trader Journal chrome |
| 017 | done | Beliefs chrome |
| 018 | skipped | Alerts already had no xl/lg card chrome |
| 019 | done | Chart Saves chrome |
| 020 | done | Backtest chrome |
| 021 | done | Options Backtest chrome |
| 022 | done | GEX Replay chrome |
| 023 | done | Trident chrome; sticky overflow axes kept |
| 024 | skipped | No xl/lg; heatmap requires overflow-x-auto rounded-[8px] |
| 025 | done | News chrome |
| 026 | done | Ask Cipher chrome |
| 027 | done | Settings chrome |
| 028 | done | Operator Status chrome |
| 029 | done | Gold focus-within on ticker search; focus-visible on nav, workspaces, watchlist, suggestions, profile. Web 76 expected. Sync OK. |
| 030 | done | Gold focus-visible on all seven Sidebar buttons. Guest nav filter unchanged. Sync OK. |
| 031 | done | Palette dialog + gold input/item focus; guest still cannot open it. Sync OK. |
| 032 | done | Auth email/password htmlFor+id+name; autocomplete kept; gold focus. Guest path unchanged. Sync OK. |
| 033 | done | Shared Button focus is Cipher gold, not shadcn ring-ring. Sync OK. |
| 034 | done | CSV download named; decorative DownloadIcon hidden; history rows labelled. Scoring unchanged. |
| 035 | done | Refresh icon hidden from AT; timeframe group/pills and strike rows labelled. Geometry untouched. |
| 036 | done | Strike Matrix pills are radiogroups with ArrowLeft/Right; gold focus. Heatmap cells unchanged. |
| 037 | done | Spyglass Bio/Contract Search tablist with arrows; Bio stays in tab order on default view; side inference unchanged. |
| 038 | done | Dialogs and mobile nav drawer use overscroll-behavior: contain; heatmap scrollports unchanged. |
| 039 | skipped | Skip-link already targets `#cipher-workspace` on both `<main>` branches; tabIndex=-1. |
| 040 | done | Reduced-motion zeros remaining transitions and spin/pulse; skeleton reveal delay kept. |
| 041 | done | Settings refresh interval is a labelled radiogroup; provider fields have htmlFor/id; session-only. |
| 042 | skipped | Heatmap cell names already locked (Matrix/Trident/GEX Replay); both HeatmapCell sites pass ariaLabel. |
| 043 | done | Text loading states use polite aria-live; Holdings still has no skeleton. |
| 044 | done | Sr-only h1 on dense panels that lacked headings; nested guest showcase uses h2. |
| 045 | done | Header/sidebar/Night Vision/Chart Saves icon chrome is 32px; heatmap cells stay 26px. |
| 046 | done | Purple leftover hex/copy gone from web/src; heatmap legend says amber; catalog BLOCKED uses gold tokens. |
| 047 | done | Scanner score labelled structural /100; intro says not P(profit); CSV and table headers match. |
| 048 | done | GEX public-OI heuristic on heatmap legend, Morning Brief, Replay empty, Alerts, guest Matrix/Trident. |
| 049 | done | Earnings Radar, paper scorecard, and guest fallback show UNVALIDATED_FOR_LIVE_OPTIONS_PNL; gate token not space-rewritten. |
| 050 | done | Paper Portfolios and Morning Brief label captured fills/mids; estimated heuristic premiums stay on Earnings Radar. |
| 051 | done | Skew Map badge is PROVISIONAL when sessions < 20; session count shown; no WoW/earnings join. |
| 052 | done | Missing NV GEX/VEX is unknown, not $0; geometry file and domain extras unchanged. |
| 053 | done | Guest ticker tape labelled demo, not live; header and workbench quotes labelled live. Catalog unchanged. |
| 054 | done | HEALTHY_NO_SETUP zero paper trades labelled healthy; guest Autopilot demo says zero-trade can be correct. |
| 055 | done | Workbench Overview: no order ticket; signed-in Options jump no longer says executable. |
| 056 | done | Yahoo/yfinance quotes and chains labelled delayed; guest live suffix skipped on Yahoo. |
| 057 | done | Matrix, Trident, heatmap legend, and Options Terminal show OI as of date or unknown. |
| 058 | done | Spyglass scanning and Flow Tape loading copy use typographic ellipsis. |
| 059 | done | Finished scan with zero names is an empty job, not a failed scan or filter miss. |
| 060 | done | Locked guest panels share one boundary sentence; catalog modes unchanged. |
| 061 | skipped | Guest Workbench Flow tab already covered by ticker-workbench-ui and siblings. |
| 062 | skipped | Night Vision chrome already covered by night-vision-ui.test.mjs from loop 002. |
| 063 | skipped | Scanner already asserts no order identifiers in setup-scanner-ui and siblings. |
| 064 | done | web/src walk forbids live and paper Alpaca broker hosts. |
| 065 | skipped | Trident/Replay GEX caveat already asserted via ExposureLegend and gex-heuristic-copy. |
| 066 | done | Hosted guest catalog E2E passed desktop+mobile after UI loops 046–060. |
| 067 | done | Workbench Options chain is an internal scrollport; hosted overflow 0 on desktop+mobile. |
| 068 | done | Guest Night Vision hybrid (standalone + Workbench Chart) overflow 0; geometry untouched. |
| 069 | skipped | app/ not touched this program; skip-if on 069. |
| 070 | skipped | Night Vision has no sticky descendants; Matrix/Trident both-axes invariant already tested. |
| 071 | skipped | Setup Scanner has no sticky descendants; results overflow is loop 076. |
| 072 | skipped | Paper ledger has no sticky descendants; table overflow is loop 079. |
| 073 | skipped | Earnings Radar has no sticky; overflow-x and overflow-y already asserted. |
| 074 | skipped | Backtest results have no sticky descendants; table overflow is loop 082. |
| 075 | skipped | Compact Morning Brief already asserted in product-hardening.test.mjs. |
| 076 | done | Ranked scan results are a labelled overflow-x/y scrollport, not clipped. |
| 077 | done | Strike Matrix grid-scroll is a named region; overflow-auto and 26px cells kept. |
| 078 | done | Spyglass flow and contract-search tapes are named overflow-x-auto regions; 560px min-width kept. |
| 079 | done | Paper ledger tables are named overflow-x/y regions; captured vs estimated marks unchanged. |
| 080 | done | Earnings cards table is a named overflow-x/y region; UNVALIDATED gate unchanged. |
| 081 | done | Journal entries wrap in a named overflow-x/y region; MFE/MAE still not option-premium P/L. |
| 082 | done | Backtest partition table is a named overflow-x/y region; next-open stop-first copy unchanged. |
| 083 | done | Holdings open/option/closed grids are named overflow-x/y regions; no broker copy; no skeleton. |
| 084 | done | Strategy catalog verdict register is a named region; overflow-x-auto rounded-[8px] and 760px min-width kept. |
| 085 | done | News headline list is a named overflow-x/y region; caveat verbatim; hosted guest E2E passed after 081–085. |
| 086 | done | Guest Night Vision source is live vs demo; loading no longer claims live; retry on fallback; geometry untouched. |
| 087 | done | Guest Strike Matrix source is live vs demo; loading no longer claims live; retry on fallback; 26px cells and overflow-auto kept. |
| 088 | done | Guest Options Terminal nav stays a labelled demo showcase; live chain pointed at Ticker Workbench Options; catalog/PanelHost unchanged. |
| 089 | done | Guest Workbench Options mounts the live chain and is labelled live vs the demo nav; Flow stays locked; no order ticket. |
| 090 | skipped | PanelHost already routes non-hybrid/non-live guest panels to GuestShowcase; filter already asserted. |
| 091 | skipped | Ask Cipher already locked for guests with the shared boundary sentence; Workbench Agent stays signed-in only. |
| 092 | skipped | Holdings already locked for guests with the shared boundary sentence; still no holdings skeleton. |
| 093 | skipped | Alerts already locked for guests with the shared boundary sentence. |
| 094 | skipped | Guest nav already omits SYSTEM; catalog and E2E already pin Operator Status / Settings at zero. |
| 095 | skipped | Guest universe already pinned to the 12 names in GUEST_TICKERS and guest-catalog. |
| 096 | done | Evidence-only audit of 001–095 in docs/loops/AUDIT_2026-08-23.md. |
| 097 | skipped | Daily workflow line still matches current panel labels; 088/089 did not rename panels. |
| 098 | skipped | DESIGN.md already covers labelled scroll regions and distinct demo/live states. |
| 099 | skipped | LEDGER already lists each skipped ID with a reason. |
| 100 | done | Stop. Further IDs would only be padding. No 101st loop. |

Completed count: 76
Skipped count: 24
Next: none

