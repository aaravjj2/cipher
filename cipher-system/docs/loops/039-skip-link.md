# Loop 039 — skip (already true)

**Queued claim:** Skip-link lands in main.

**Evidence (no code change):**
- `page.tsx` first child is `<a className="cipher-skip-link" href="#cipher-workspace">Skip to workspace</a>`.
- Both tiled and panel `<main>` elements use `id="cipher-workspace"` and `tabIndex={-1}` so hash focus can land.
- `globals.css` reveals the control on `:focus`.

Skip-if is true. Do not add a second skip link or retarget `#cipher-workspace`.
