# Cipher terminal design contract

Derived selectively from Open Design's `trading-terminal` system:

- Desktop-first, dense grid panels, stable numeric typography, and border-defined surfaces.
- No decorative rounded cards or animated data flicker.
- Visible data gaps are labelled instead of filled with synthetic values.

Cipher-specific visual constraints from the reference app:

- Background `#07090e`, charcoal panels, thin blue-black separators.
- Magenta/purple for positive exposure, red for negative exposure, yellow for the global peak, off-white spot marker.
- Top header: 29px; left rail: 116px; Matrix values: compact 7–8px data typography.
- Matrix and Night Vision remain two distinct work surfaces.

Data integrity:

- GEX requires both gamma and open interest. Missing input is shown as unknown; never as zero.
- No order, paper-trading, or execution affordances belong in this UI.