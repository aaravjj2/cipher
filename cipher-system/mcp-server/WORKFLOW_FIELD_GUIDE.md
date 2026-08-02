# Daily workflow field guide

Use these exact `screen` values in `record_observation` when `complete_step` is `true`:

| Visible Cipher surface | `screen` value | Capture at minimum |
| --- | --- | --- |
| Strike Matrix | `strike_matrix` | spot, GEX/VEX selection, gamma flip, call wall, put wall, expiry context |
| Night Vision with X-Ray | `night_vision` | timeframe, visible price context, hot strikes, support/resistance, chart capture |
| Spyglass / Flow | `spyglass` | active filters, visible prints, quote-side labels, uncertainty |
| Setup Scanner | `scanner` | scanner horizon, labels/rank, visible evidence and missing data |
| Cross-screen synthesis | `synthesis` | agreement, disagreement, invalidation, and no-trade caveat |

The display label can remain in the note. The exact ID above lets `workflow_status` advance the matching step.
