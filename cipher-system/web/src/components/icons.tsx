// Deduplicated inline SVG icons extracted from https://www.accessobsidian.com/app
// Names are best-guess by visual/semantic function — refine per-component during Phase 3 builds.

import type { SVGProps } from "react";

export function MenuIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" {...props}>
      <path fill="currentColor" d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z" />
    </svg>
  );
}

export function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" {...props}>
      <path
        fill="currentColor"
        d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14"
      />
    </svg>
  );
}

export function StrikeMatrixIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M3 3h2v16h16v2H3zm4 10h2v4H7zm4-6h2v10h-2zm4 3h2v7h-2zm4-6h2v13h-2z"
      />
    </svg>
  );
}

export function RefreshIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M17.65 6.35A8 8 0 1 0 19.73 14h-2.08A6 6 0 1 1 12 6a5.9 5.9 0 0 1 4.22 1.78L13 11h7V4z"
      />
    </svg>
  );
}

export function ChevronLeftIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M14 7l-5 5 5 5z" />
    </svg>
  );
}

export function GridIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M3 3h8v8H3zm10 0h8v8h-8zM3 13h8v8H3zm10 0h8v8h-8z" />
    </svg>
  );
}

export function StarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M12 17.27 18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"
      />
    </svg>
  );
}

export function JournalIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M7 2h10a2 2 0 0 1 2 2v16l-3-2-2 2-2-2-2 2-2-2-3 2V4a2 2 0 0 1 2-2m1 5v2h8V7zm0 4v2h8v-2zm0 4v2h5v-2z"
      />
    </svg>
  );
}

export function PortfolioIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M9 3h6a2 2 0 0 1 2 2v1h2a2 2 0 0 1 2 2v3H3V8a2 2 0 0 1 2-2h2V5a2 2 0 0 1 2-2m0 2v1h6V5zm-6 8h18v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zm8 1v3h2v-3z"
      />
    </svg>
  );
}

export function ChatIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9l-4.4 3.3A1 1 0 0 1 3 19.5V5a1 1 0 0 1 1-1m3 5v2h10V9zm0 4v2h7v-2z"
      />
    </svg>
  );
}

export function TridentIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M11 2h2v3.2a4 4 0 0 1 2.86 3.55L16 9h3V6h2v4a1 1 0 0 1-1 1h-4v2.27a2 2 0 1 1-2 0V11h-4v2.27a2 2 0 1 1-2 0V11H4a1 1 0 0 1-1-1V6h2v3h3l.14-.25A4 4 0 0 1 11 5.2z"
      />
    </svg>
  );
}

export function BookmarkIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M17 3H7a2 2 0 0 0-2 2v16l7-3 7 3V5a2 2 0 0 0-2-2" />
    </svg>
  );
}

export function ScannerIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M11 2a9 9 0 1 0 5.6 16.04l4.18 4.18 1.41-1.41-4.18-4.18A9 9 0 0 0 11 2m0 2a7 7 0 1 1 0 14 7 7 0 0 1 0-14"
      />
    </svg>
  );
}

export function SettingsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M3 3h18v2H3zm2 4h14v14H5zm3 3v8h2v-8zm4 2v6h2v-6zm4-2v8h2v-8z" />
    </svg>
  );
}

export function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M3 17l6-6 4 4 8-8-1.4-1.4L13 12.2l-4-4L2 15.6z" />
    </svg>
  );
}

export function BoltIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M13 2 4.5 12.5H11l-1 9L19.5 11H13z" />
    </svg>
  );
}

// New icon (no existing match in the source extraction): simple line/trend chart,
// used for the "Night Vision" nav item.
export function NightVisionIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M3 3h2v16h16v2H3zm3.5 11.5 4-4.5 3 3 6-7 1.5 1.3-7.4 8.6-3-3-3 3.5z"
      />
    </svg>
  );
}

// New icon (no existing match in the source extraction): simple export/download glyph,
// used for the Strike Matrix corner-cell export button.
export function DownloadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" {...props}>
      <path fill="currentColor" d="M11 3h2v9.2l3.1-3.1 1.4 1.4L12 15 6.5 9.5l1.4-1.4L11 12.2zM5 19h14v2H5z" />
    </svg>
  );
}

// New icon (no existing match in the source extraction): simple downward chevron,
// used for the Trident expiration-selector dropdown trigger.
export function ChevronDownIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" {...props}>
      <path fill="currentColor" d="M7 10l5 5 5-5z" />
    </svg>
  );
}

// New icon (no existing match in the source extraction): crown glyph, used for the
// Settings "Your plan" card heading.
export function CrownIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M3 8l4 3 5-6 5 6 4-3-1.5 11h-15zM5.2 21h13.6v2H5.2z"
      />
    </svg>
  );
}

// New icon (no existing match in the source extraction): clock glyph, used for the
// Settings "Preferences" card heading.
export function ClockIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2m0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8m.75-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"
      />
    </svg>
  );
}

// New icon (no existing match in the source extraction): key glyph, used for the
// Settings "Connect API" card heading.
export function KeyIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M14.5 2a6.5 6.5 0 0 0-6.32 8.03L2 16.2V21h4.8l1.13-1.13V18h1.87v-1.87h1.87l1.83-1.83A6.5 6.5 0 1 0 14.5 2m2.5 6a2 2 0 1 1 2-2 2 2 0 0 1-2 2"
      />
    </svg>
  );
}

// New icon (no existing match in the source extraction): upload/export glyph (upward arrow
// out of a tray), used for the Journal panel's "Save image" button.
export function UploadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" {...props}>
      <path fill="currentColor" d="M11 15h2V5.8l3.1 3.1 1.4-1.4L12 2 6.5 7.5l1.4 1.4L11 5.8zM5 19h14v2H5z" />
    </svg>
  );
}

// New icon (no existing match in the source extraction): right-pointing chevron, used
// alongside ChevronLeftIcon for the Journal panel's month navigator.
export function ChevronRightIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path fill="currentColor" d="M10 7l5 5-5 5z" />
    </svg>
  );
}

/** Newspaper — the News panel. Same flat single-path currentColor style as the rest. */
export function NewsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M4 4h13a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zm2 2v3h9V6zm0 5v2h9v-2zm0 4v2h6v-2zm13-7h1a1 1 0 0 1 1 1v10a1 1 0 0 1-2 0z"
      />
    </svg>
  );
}

/** Historical options datasets and stored backtest reports. */
export function OptionsBacktestIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" {...props}>
      <path
        fill="currentColor"
        d="M4 3h12l4 4v14H4zm2 2v14h12V8h-3V5zm2 10h2v2H8zm3-4h2v6h-2zm3-2h2v8h-2z"
      />
    </svg>
  );
}
