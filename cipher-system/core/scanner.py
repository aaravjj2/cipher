"""Setup Scanner — Cipher Model reconstruction from Access Obsidian audit.

Cipher Model Scan card schema (observed):
  ticker, direction (BULLISH|BEARISH), score 0–100,
  supports[], resistances[], pull_target, vacuum_targets[],
  close_under / reclaim invalidation, narrative `read`.

Also supports Liq / Cluster strategies and horizon modes
(short ~15d, long multi-exp, LEAP ~120d).

GEX is a dealer-positioning heuristic from OI × gamma — research only.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

import agentic_episodes

# Cap-tier universe from data/optionable_universe_by_cap.json.
# Confirmed against the real product's own Setup Scanner (paired comparison, 2026-08-06):
# it scans ~578/580 of the same optionable universe (excludes only ~2 non-optionable
# names), not the mega+large+medium-only cutoff this used to apply. Scanning all tiers
# matches that — small/unknown-cap names may have thinner OI (noisier GEX), same
# public-OI-heuristic caveat as everything else here, not a reason to drop them outright.
_UNIVERSE_JSON = Path(__file__).resolve().parents[1] / "data" / "optionable_universe_by_cap.json"
_UNIVERSE_INCLUDED_TIERS = ("mega", "large", "medium", "small", "unknown")
_UNIVERSE_EXCLUDED_TIERS = ()
UNIVERSE_CUTOFF = (
    "Scans the full optionable universe (all cap tiers) — matches the real product's "
    "near-total inclusion. Small/unknown-cap names may have thinner OI/noisier GEX."
)

# Fallback liquid subset if the JSON is missing (original faster list).
_RAW_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA", "RSP", "OEF", "IVV", "VOO", "VTI", "TQQQ",
    "SQQQ", "SOXL", "SOXS", "UVXY", "VXX", "HYG", "LQD", "TLT", "IEF", "SHY",
    "TIP", "GLD", "SLV", "USO", "XLE", "XLF", "XLK", "XLV", "XLI", "XLY",
    "XLP", "XLU", "XLRE", "XLB", "XLC", "SMH", "XBI", "ARKK", "IBIT", "AAPL",
    "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "ORCL", "CRM", "ADBE", "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "TXN",
    "ASML", "TSM", "ARM", "SMCI", "DELL", "HPQ", "HPE", "IBM", "CSCO", "ANET",
    "MRVL", "ON", "NXPI", "ADI", "MCHP", "SWKS", "QRVO", "MPWR", "ENTG", "TER",
    "GFS", "UMC", "WDC", "STX", "NTAP", "SNDK", "PLTR", "SNOW", "MDB", "DDOG",
    "NET", "CRWD", "ZS", "PANW", "FTNT", "OKTA", "CYBR", "ESTC", "NOW", "WDAY",
    "TEAM", "PATH", "AI", "APP", "U", "RBLX", "SNAP", "PINS", "MTCH", "SPOT",
    "DASH", "CART", "UBER", "LYFT", "ABNB", "BKNG", "EXPE", "MAR", "HLT", "COIN",
    "HOOD", "SOFI", "AFRM", "UPST", "PYPL", "XYZ", "V", "MA", "AXP", "COF",
    "DFS", "SYF", "ALLY", "SQ", "JPM", "BAC", "WFC", "C", "GS", "MS",
    "BLK", "SCHW", "BX", "KKR", "APO", "AMP", "TROW", "XOM", "CVX", "COP",
    "OXY", "EOG", "DVN", "FANG", "MPC", "VLO", "PSX", "SLB", "HAL", "BKR",
    "APA", "HES", "BA", "LMT", "RTX", "GD", "NOC", "LHX", "TDG", "HII",
    "GE", "CAT", "DE", "CMI", "PCAR", "URI", "FAST", "GWW", "IR", "PH",
    "EMR", "HON", "UNP", "CSX", "NSC", "FDX", "UPS", "DAL", "UAL", "AAL",
    "LUV", "HD", "LOW", "COST", "WMT", "TGT", "DG", "DLTR", "KR", "BBY",
    "TJX", "ROST", "NKE", "LULU", "DECK", "SBUX", "MCD", "CMG", "YUM", "DPZ",
    "QSR", "WING", "SHAK", "CAVA", "BROS", "UNH", "CI", "ELV", "HUM", "CVS",
    "CNC", "HCA", "JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN", "GILD",
    "BIIB", "REGN", "VRTX", "ISRG", "MDT", "SYK", "BSX", "EW", "ZBH", "MRNA",
    "BNTX", "DIS", "CMCSA", "WBD", "CHTR", "T", "VZ", "TMUS", "EA", "TTWO",
    "F", "GM", "RIVN", "LCID", "STLA", "TM", "NIO", "XPEV", "LI", "RACE",
    "KMX", "CVNA", "AN", "BAX", "BDX", "TMO", "DHR", "A", "IQV", "ILMN",
    "DXCM", "PODD", "PEP", "KO", "MNST", "KDP", "MDLZ", "GIS", "K", "HSY",
    "SYY", "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL", "WEC",
    "PCG", "EIX", "ED", "AEE", "CMS", "CNP", "AMT", "EQIX", "DLR", "CCI",
    "PLD", "SPG", "O", "WELL", "VICI", "ASTS", "RKLB", "IONQ", "JOBY", "ACHR",
    "LUNR", "OKLO", "SMR", "CCJ", "UEC", "DNN", "LEU", "BWXT", "GEV", "VST",
    "CEG", "TLN", "NRG", "BE", "ENVX", "QS", "PLUG", "RIOT", "MARA", "CLSK",
    "IREN", "HUT", "CIFR", "BITF", "MSTR", "LAES", "IOVA", "AXTI", "HIVE", "NUAI",
    "CLOV", "WULF", "POET", "MNKD", "BULL", "EOSE", "HTZ", "CRDO", "ALAB", "TWLO",
    "SHOP", "MELI", "SE", "BABA", "JD", "PDD", "BIDU", "NU", "GRAB", "TIGR",
    "FUTU", "BILI", "ZM", "DOCU", "ROKU", "NBIS", "INTU", "ADP", "PAYX", "FIS",
    "FISV", "GPN", "JKHY", "PM", "MO", "BTI", "CL", "PG", "KMB", "CHD",
    "SHW", "ECL", "APD", "LIN", "AIR", "FCX", "NEM", "GOLD", "AA", "X",
    "CLF", "NUE", "STLD", "RCL", "CCL", "NCLH", "H", "CME", "ICE", "NDAQ",
    "CBOE", "SPGI", "MCO", "VRSK", "EFX", "TRU", "MMM", "ITW", "ROK", "ABT",
    "ZTS", "IDXX", "ALGN", "HOLX", "WBA", "MOH", "BMRN", "ALNY", "SRPT", "IONS",
    "NBIX", "INCY", "EXEL", "BGNE", "ACAD", "AXSM", "ITCI", "TXG", "ADM", "ADSK",
    "AES", "AFL", "AIG", "AJG", "AKAM", "ALB", "ALL", "AMCR", "ANSS", "APH",
    "APTV", "ARE", "ARES", "ATO", "AVB", "AVY", "AWK", "AZO", "BALL", "BBWI",
    "BEN", "BG", "BIO", "BK", "BLDR", "BR", "BRO", "BSY", "BWA", "CAG",
    "CAH", "CARR", "CB", "CBRE", "CDNS", "CDW", "CE", "CF", "CFG", "CHRW",
    "CINF", "CLX", "COO", "COR", "CPB", "CPRT", "CPT", "CRL", "CSGP", "CTAS",
    "CTSH", "CTVA", "DAY", "DD", "DGX", "DHI", "DOC", "DOV", "DOW", "DRI",
    "DTE", "DVA", "EBAY", "EL", "ENPH", "EPAM", "EQR", "EQT", "ES", "ESS",
    "ETN", "ETR", "EVRG", "EXPD", "EXR", "FDS", "FE", "FFIV", "FITB", "FMC",
    "FSLR", "FTV", "GL", "GLW", "GNRC", "GPC", "GRMN", "HAS", "HBAN", "HIG",
    "HRL", "HSIC", "HST", "HUBB", "HWM", "IEX", "IFF", "INVH", "IP", "IPG",
    "IRM", "IT", "IVZ", "J", "JBHT", "JBL", "JCI", "JNPR", "JWN", "KEY",
    "KEYS", "KHC", "KIM", "KMI", "KVUE", "L", "LDOS", "LEN", "LH", "LKQ",
    "LNT", "LVS", "LW", "LYB", "LYV", "MAA", "MAS", "MCK", "MET", "MGM",
    "MHK", "MKC", "MKTX", "MLM", "MMC", "MOS", "MRO", "MSCI", "MSI", "MTB",
    "MTD", "NDSN", "NI", "NTRS", "NVR", "ODFL", "OKE", "OMC", "ORLY", "OTIS",
]


def _dedupe(tickers):
    seen = set()
    out = []
    for t in tickers:
        t = str(t).upper().strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _load_scan_universe():
    """Load optionable universe by cap; exclude small + unknown tiers.

    Returns (tickers, meta). Falls back to _RAW_UNIVERSE if JSON is unavailable.
    """
    meta = {
        "source": "fallback_raw",
        "path": str(_UNIVERSE_JSON),
        "raw_count": None,
        "filtered_count": None,
        "included_tiers": list(_UNIVERSE_INCLUDED_TIERS),
        "excluded_tiers": list(_UNIVERSE_EXCLUDED_TIERS),
        "cutoff": UNIVERSE_CUTOFF,
        "tier_counts": {},
        "excluded_counts": {},
    }
    if not _UNIVERSE_JSON.is_file():
        tickers = _dedupe(_RAW_UNIVERSE)
        meta["filtered_count"] = len(tickers)
        return tickers, meta

    try:
        payload = json.loads(_UNIVERSE_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        tickers = _dedupe(_RAW_UNIVERSE)
        meta["source"] = "fallback_raw"
        meta["error"] = str(exc)
        meta["filtered_count"] = len(tickers)
        return tickers, meta

    sorted_tickers = payload.get("sorted_tickers") or {}
    raw_count = int(payload.get("count") or sum(len(sorted_tickers.get(t) or []) for t in sorted_tickers))
    meta["source"] = "optionable_universe_by_cap.json"
    meta["raw_count"] = raw_count
    meta["as_of"] = payload.get("as_of")
    meta["thresholds_usd"] = payload.get("thresholds_usd") or {}

    included = []
    for tier in _UNIVERSE_INCLUDED_TIERS:
        names = list(sorted_tickers.get(tier) or [])
        meta["tier_counts"][tier] = len(names)
        included.extend(names)
    for tier in _UNIVERSE_EXCLUDED_TIERS:
        meta["excluded_counts"][tier] = len(list(sorted_tickers.get(tier) or []))

    tickers = _dedupe(included)
    meta["filtered_count"] = len(tickers)
    return tickers, meta


SCAN_UNIVERSE, UNIVERSE_META = _load_scan_universe()

MODE_CONFIG = {
    # Setup Scanner (`analyze_ticker`) always fetches nearest/lowest-DTE (exp_count=1).
    # `expirations` here is retained for non-scanner callers (e.g. weight_lab); Short/Long/LEAP
    # still differ by strike depth / chain pages / scoring hints in the scanner UI.
    "short": {
        "depth": 0.06,
        "expirations": 1,
        "label": "Short term (~15 market days)",
        "pages": 2,
        "hint": "Short term — nearest/lowest-DTE expiration only; tighter strike window.",
    },
    "long": {
        "depth": 0.10,
        "expirations": 5,
        "label": "Long term",
        "pages": 3,
        "hint": "Long term — nearest/lowest-DTE expiration only; wider strike window for structure.",
    },
    "leap": {
        "depth": 0.15,
        "expirations": 36,
        "label": "LEAP (~120 market days)",
        "pages": 4,
        "hint": "LEAP — nearest/lowest-DTE expiration only; widest strike window (scoring label, not multi-exp fetch).",
    },
}

# Flash BETA: 12 most-liquid names (audit tooltip). Flash Index = SPY/QQQ/IWM.
FLASH_UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AMZN", "META", "GOOGL", "NFLX",
]
FLASH_INDEX_UNIVERSE = ["SPY", "QQQ", "IWM"]

FLASH_HINT = (
    "Flash is an intraday model using nearest-expiration GEX/VEX floors and ceilings, "
    "session key levels, volume-profile proxies, VWAP/momentum, touch/reaction, and flow. "
    "First targets are ATR-scaled rungs; structural wall is the stretch target."
)
FLASH_INDEX_HINT = "Flash Index applies the Flash model to QQQ, SPY, and IWM only. Not logged — live market pulse."
FLASH_AGENTIC_HINT = (
    "Flash Agentic watches liquid names and surfaces only armed/triggered setups "
    "(break/reject confirmation, price at trigger, target progression)."
)

PROMINECE_PCT = 0.07
MIN_LEVEL_ABS_FRAC = 0.05

_SCAN_CACHE: dict = {}
_SCAN_JOBS: dict = {}
_JOB_LOCK = threading.Lock()


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _fmt_level(x):
    if x is None:
        return "?"
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _strike_profile(rows, expiration_index=None, expirations=None, cluster_exp=None):
    """Aggregate GEX by strike. Optionally restrict to one expiration column.

    cluster_exp: 'nearest' | ISO date matching an expiration label | int index | None (all).
    """
    exp_idx = expiration_index
    # Handle integer index directly (e.g., 1 for 2nd expiration)
    if isinstance(cluster_exp, int):
        exp_idx = cluster_exp
    elif cluster_exp and cluster_exp not in {"nearest", "", "all", None} and expirations:
        want = str(cluster_exp)[:10]
        for i, exp in enumerate(expirations):
            label = exp if isinstance(exp, str) else (exp.get("expiration") or exp.get("date") or "")
            if str(label)[:10] == want:
                exp_idx = i
                break
    elif cluster_exp == "nearest":
        exp_idx = 0

    profile = []
    for row in rows or []:
        cells = row.get("cells") or []
        if exp_idx is not None:
            if exp_idx < 0 or exp_idx >= len(cells):
                continue
            cells = [cells[exp_idx]]
        if not any(cell.get("available") for cell in cells):
            continue
        call = sum(cell.get("call_gex") or 0.0 for cell in cells)
        put = sum(cell.get("put_gex") or 0.0 for cell in cells)
        net = call + put
        vex = sum((cell.get("net_vex") or 0.0) for cell in cells)
        oi = sum((cell.get("call_oi") or 0.0) + (cell.get("put_oi") or 0.0) for cell in cells)
        vol = sum(cell.get("volume") or 0.0 for cell in cells)
        profile.append(
            {
                "strike": float(row["strike"]),
                "call": call,
                "put": put,
                "net": net,
                "abs": abs(net),
                "abs_vex": abs(vex),
                "vex": vex,
                "oi": oi,
                "volume": vol,
            }
        )
    profile.sort(key=lambda item: item["strike"])
    return profile


def _local_peaks(profile, prominence_pct=PROMINECE_PCT, distance=2):
    if len(profile) < 3:
        return []
    nets = np.array([p["net"] for p in profile], dtype=float)
    abs_gex = np.abs(nets)
    peak_abs = float(abs_gex.max())
    if peak_abs <= 0:
        return []
    idxs, props = find_peaks(abs_gex, prominence=peak_abs * prominence_pct, distance=max(1, distance))
    prominences = props.get("prominences", np.zeros(len(idxs)))
    peaks = []
    seen = set()
    for i, idx in enumerate(idxs):
        item = profile[int(idx)]
        peaks.append(
            {
                "strike": item["strike"],
                "net": item["net"],
                "abs": item["abs"],
                "call": item["call"],
                "put": item["put"],
                "oi": item["oi"],
                "prominence": float(prominences[i]) if i < len(prominences) else 0.0,
                "index": int(idx),
                "is_golden": False,
            }
        )
        seen.add(item["strike"])
    gidx = int(np.argmax(abs_gex))
    golden = profile[gidx]
    if golden["strike"] not in seen:
        peaks.append(
            {
                "strike": golden["strike"],
                "net": golden["net"],
                "abs": golden["abs"],
                "call": golden["call"],
                "put": golden["put"],
                "oi": golden["oi"],
                "prominence": golden["abs"],
                "index": gidx,
                "is_golden": True,
            }
        )
    else:
        for peak in peaks:
            if peak["strike"] == golden["strike"]:
                peak["is_golden"] = True
    peaks.sort(key=lambda p: p["abs"], reverse=True)
    return peaks


def _pick_levels(peaks, spot, side, limit=2, max_dist_pct=0.25):
    """Pick strongest peaks on one side of spot, nearest-first among top strength."""
    if side == "below":
        cands = [p for p in peaks if p["strike"] < spot * 0.999]
    else:
        cands = [p for p in peaks if p["strike"] > spot * 1.001]
    cands = [p for p in cands if abs(p["strike"] / spot - 1.0) <= max_dist_pct]
    if not cands:
        return []
    # Prefer strength, then proximity
    cands = sorted(cands, key=lambda p: (-p["abs"], abs(p["strike"] - spot)))
    top = cands[: max(limit * 2, limit)]
    # For supports: nearest-first among strong; for resistances same
    top = sorted(top, key=lambda p: abs(p["strike"] - spot))[:limit]
    if side == "below":
        top = sorted(top, key=lambda p: -p["strike"])  # nearest support first
    else:
        top = sorted(top, key=lambda p: p["strike"])
    return top


def _vacuum_targets(profile, spot, direction, first_resistance, pull_target, first_support, peak_abs):
    """Thin-liquidity runway levels between ceiling and extended pull (audit vocabulary)."""
    if not profile or peak_abs <= 0:
        return []
    thin = peak_abs * 0.18
    vacs = []
    if direction == "BULLISH" and first_resistance is not None and pull_target is not None:
        lo = min(first_resistance, pull_target)
        hi = max(pull_target * 1.02, first_resistance)
        # Include pull and extension levels that are relatively thin OR stacked magnets
        for p in profile:
            if lo <= p["strike"] <= hi * 1.15:
                if p["abs"] <= thin or p["strike"] == pull_target:
                    vacs.append(p["strike"])
        # Always include pull + a farther extension if present
        if pull_target not in vacs:
            vacs.append(pull_target)
        farther = [
            p["strike"]
            for p in profile
            if p["strike"] > pull_target and p["strike"] <= pull_target * 1.35 and p["abs"] >= peak_abs * 0.12
        ]
        for s in farther[:3]:
            if s not in vacs:
                vacs.append(s)
        vacs = sorted(set(vacs))[:4]
    elif direction == "BEARISH" and first_support is not None:
        target = pull_target if pull_target is not None else first_support
        for p in profile:
            if p["strike"] <= spot and p["strike"] >= target * 0.85:
                if p["abs"] <= thin or abs(p["strike"] - target) < 1e-9:
                    vacs.append(p["strike"])
        vacs = sorted(set(vacs))[:4]
    return vacs


def _build_read(ticker, direction, supports, resistances, pull_target, vacuum_targets, close_under, reclaim):
    if direction == "BULLISH" and supports and resistances:
        s0 = _fmt_level(supports[0])
        r0 = _fmt_level(resistances[0])
        pull = _fmt_level(pull_target)
        inv = _fmt_level(close_under)
        last_vac = _fmt_level(vacuum_targets[-1]) if vacuum_targets else None
        if last_vac and pull_target is not None and vacuum_targets and vacuum_targets[-1] != pull_target:
            return (
                f"{ticker} is holding above support at {s0} and pressing into the {r0} ceiling; "
                f"a push through opens room toward the {pull} pull target, with continuation into {last_vac} "
                f"if it keeps running. A close back under {inv} would weaken the setup."
            )
        return (
            f"{ticker} is holding above support at {s0} and pressing into the {r0} ceiling; "
            f"a push through opens room toward the {pull} pull target. "
            f"A close back under {inv} would weaken the setup."
        )
    if direction == "BEARISH" and resistances and supports:
        r0 = _fmt_level(resistances[0])
        s0 = _fmt_level(supports[0])
        pull = _fmt_level(pull_target if pull_target is not None else supports[0])
        inv = _fmt_level(reclaim)
        return (
            f"{ticker} is capped at resistance around {r0} and leaning on {s0} support; "
            f"a break of {s0} opens downside toward the {pull} pull target. "
            f"Reclaiming {inv} would invalidate the setup."
        )
    return f"{ticker} lacks a clean support/resistance structure for a Cipher Model read."


def cipher_model_from_profile(ticker, profile, peaks, summary, spot):
    """Build original Cipher Model card fields from GEX strike profile."""
    if not profile or not spot or not peaks:
        return None

    peak_abs = peaks[0]["abs"] or 1.0
    material = [p for p in peaks if p["abs"] >= peak_abs * MIN_LEVEL_ABS_FRAC]
    if len(material) < 2:
        material = peaks[:4]

    supports_p = _pick_levels(material, spot, "below", limit=2)
    resistances_p = _pick_levels(material, spot, "above", limit=2)

    # Fall back to walls from summary
    if not supports_p and summary.get("put_wall_strike"):
        supports_p = [{"strike": float(summary["put_wall_strike"]), "abs": peak_abs * 0.5, "net": -peak_abs * 0.5}]
    if not resistances_p and summary.get("call_wall_strike"):
        resistances_p = [{"strike": float(summary["call_wall_strike"]), "abs": peak_abs * 0.5, "net": peak_abs * 0.5}]

    if not supports_p and not resistances_p:
        return None

    supports = [p["strike"] for p in supports_p]
    resistances = [p["strike"] for p in resistances_p]
    # Enforce strict geometry: supports below spot, resistances above, no overlap
    supports = [s for s in supports if s < spot * 0.998]
    resistances = [r for r in resistances if r > spot * 1.002]
    if supports and resistances:
        resistances = [r for r in resistances if r > supports[0] + spot * 0.002]
    if not supports and not resistances:
        return None

    golden = next((p for p in peaks if p.get("is_golden")), peaks[0])

    above_strength = sum(p["abs"] for p in resistances_p if p["strike"] in resistances) if resistances else 0.0
    below_strength = sum(p["abs"] for p in supports_p if p["strike"] in supports) if supports else 0.0
    # Direction: holding above support → bullish bias when structure is playable
    if supports and resistances:
        near_support = (spot - supports[0]) / spot
        near_resist = (resistances[0] - spot) / spot
        if near_support >= 0 and near_support <= 0.08 and near_resist <= 0.12:
            direction = "BULLISH"
        elif near_resist >= 0 and near_resist <= 0.04 and golden["strike"] <= spot:
            direction = "BEARISH"
        elif golden["strike"] >= spot and golden["net"] >= 0:
            direction = "BULLISH"
        elif golden["net"] < 0 and golden["strike"] <= spot:
            direction = "BEARISH"
        else:
            direction = "BULLISH" if above_strength >= below_strength * 0.7 else "BEARISH"
    elif supports:
        direction = "BULLISH"
    else:
        direction = "BEARISH"

    if direction == "BULLISH":
        first_support = supports[0] if supports else None
        first_resistance = resistances[0] if resistances else None
        # Pull = magnet above ceiling (golden if above, else stronger far resistance / round extension)
        if golden["strike"] >= (first_resistance or spot):
            pull_target = golden["strike"]
        elif len(resistances) >= 2:
            pull_target = resistances[1] if resistances[1] > (first_resistance or 0) else resistances[0]
        else:
            pull_target = first_resistance
        # Prefer a clean round-ish extension when golden coincides with first resistance
        if first_resistance is not None and pull_target == first_resistance:
            farther = [p for p in material if p["strike"] > first_resistance * 1.01]
            if farther:
                pull_target = max(farther, key=lambda p: p["abs"])["strike"]
        close_under = first_support
        # Slight undercut invalidation when spot is clearly above support
        if first_support is not None and spot > first_support * 1.01:
            close_under = round(first_support * 0.99, 2) if first_support < 20 else first_support
        reclaim = None
        vacuum_targets = _vacuum_targets(
            profile, spot, direction, first_resistance, pull_target, first_support, peak_abs
        )
    else:
        first_support = supports[0] if supports else None
        first_resistance = resistances[0] if resistances else None
        pull_target = golden["strike"] if golden["strike"] <= spot else first_support
        close_under = None
        reclaim = round((first_resistance or spot) * 1.02, 2) if first_resistance else round(spot * 1.02, 2)
        vacuum_targets = _vacuum_targets(
            profile, spot, direction, first_resistance, pull_target, first_support, peak_abs
        )

    if not supports or not resistances:
        return None

    draft = {
        "direction": direction,
        "supports": supports,
        "resistances": resistances,
        "pull_target": pull_target,
        "vacuum_targets": vacuum_targets,
        "close_under": close_under,
        "reclaim": reclaim,
        "first_support": supports[0] if supports else None,
        "first_resistance": resistances[0] if resistances else None,
        "last_vacuum_target": vacuum_targets[-1] if vacuum_targets else None,
        "support_count": len(supports),
        "resistance_count": len(resistances),
        "vacuum_count": len(vacuum_targets),
        "golden": golden["strike"],
        "call_wall": summary.get("call_wall_strike"),
        "put_wall": summary.get("put_wall_strike"),
        "gamma_flip": summary.get("gamma_flip_level"),
    }

    score = _heuristic_cipher_score(draft, profile, spot, peak_abs, ticker)
    fitted = _try_fitted_score(draft, profile, spot)
    if fitted is not None:
        score = fitted
        draft["score_source"] = "fitted_weights"
    else:
        draft["score_source"] = "heuristic"

    draft["score"] = round(score, 1)
    draft["read"] = _build_read(
        ticker, direction, supports, resistances, pull_target, vacuum_targets, close_under, reclaim
    )
    return draft


_FITTED_WEIGHTS = None
_FITTED_ACTIVE = None
_FITTED_LOADED_AT = 0.0
_FITTED_FLASH_WEIGHTS = None
_FITTED_FLASH_ACTIVE = None
_FITTED_FLASH_LOADED_AT = 0.0


def _load_fitted():
    global _FITTED_WEIGHTS, _FITTED_ACTIVE, _FITTED_LOADED_AT
    if time.time() - _FITTED_LOADED_AT < 5 and _FITTED_ACTIVE is not None:
        return _FITTED_WEIGHTS if _FITTED_ACTIVE else None
    _FITTED_LOADED_AT = time.time()
    try:
        import weight_lab

        _FITTED_ACTIVE = weight_lab.is_active()
        _FITTED_WEIGHTS = weight_lab.load_weights() if _FITTED_ACTIVE else None
    except Exception:
        _FITTED_ACTIVE = False
        _FITTED_WEIGHTS = None
    return _FITTED_WEIGHTS if _FITTED_ACTIVE else None


def _load_fitted_flash():
    global _FITTED_FLASH_WEIGHTS, _FITTED_FLASH_ACTIVE, _FITTED_FLASH_LOADED_AT
    if time.time() - _FITTED_FLASH_LOADED_AT < 5 and _FITTED_FLASH_ACTIVE is not None:
        return _FITTED_FLASH_WEIGHTS if _FITTED_FLASH_ACTIVE else None
    _FITTED_FLASH_LOADED_AT = time.time()
    try:
        import weight_lab

        _FITTED_FLASH_ACTIVE = weight_lab.is_flash_active()
        _FITTED_FLASH_WEIGHTS = weight_lab.load_flash_weights() if _FITTED_FLASH_ACTIVE else None
    except Exception:
        _FITTED_FLASH_ACTIVE = False
        _FITTED_FLASH_WEIGHTS = None
    return _FITTED_FLASH_WEIGHTS if _FITTED_FLASH_ACTIVE else None


def _try_fitted_score(model, profile, spot, *, dte=None):
    weights = _load_fitted()
    if not weights:
        return None
    try:
        import weight_lab

        feat = weight_lab.features_from_model(model, profile, spot, dte=dte)
        if not feat:
            return None
        return weight_lab.score_features(feat, weights)
    except Exception:
        return None


def _try_fitted_flash_score(model, profile, spot, runway_clarity=None, dte=5.0):
    weights = _load_fitted_flash()
    if not weights:
        return None
    try:
        import weight_lab

        feat = weight_lab.features_from_model(
            model, profile, spot, runway_clarity=runway_clarity, dte=dte
        )
        if not feat:
            return None
        return weight_lab.score_flash_features(feat, weights)
    except Exception:
        return None


def _heuristic_cipher_score(model, profile, spot, peak_abs, ticker):
    """Hand-tuned prior — used when fitted weights are inactive."""
    score = 52.0
    supports = model.get("supports") or []
    resistances = model.get("resistances") or []
    first_support = model.get("first_support")
    first_resistance = model.get("first_resistance")
    pull_target = model.get("pull_target")
    vacuum_targets = model.get("vacuum_targets") or []
    direction = model.get("direction")

    if len(supports) >= 2:
        score += 4
    if len(resistances) >= 2:
        score += 3

    near_gap_pct = (first_resistance - first_support) / spot
    if 0.025 <= near_gap_pct <= 0.09:
        score += 7
    elif near_gap_pct <= 0.15:
        score += 3

    if direction == "BULLISH":
        dist_to_ceil = (first_resistance - spot) / spot
        dist_to_floor = (spot - first_support) / spot
        if 0 <= dist_to_floor <= 0.025:
            score += 5
        elif dist_to_floor <= 0.05:
            score += 2
        if 0 <= dist_to_ceil <= 0.025:
            score += 6
        elif dist_to_ceil <= 0.05:
            score += 3

    if vacuum_targets and pull_target:
        stretch = abs(pull_target - first_support) / spot
        if 0.10 <= stretch <= 0.28:
            score += 6
        elif 0.05 <= stretch <= 0.40:
            score += 3
        score += min(len([v for v in vacuum_targets if v != pull_target]), 2)

    avg_abs = sum(p["abs"] for p in profile) / max(len(profile), 1)
    sharpness = peak_abs / (avg_abs + 1e-9)
    score += min(max(sharpness - 1.5, 0.0) * 1.5, 5.0)

    oi = sum(p["oi"] for p in profile)
    score += min(oi / 250000.0, 2.5)

    jitter = (sum(ord(c) for c in ticker) % 9) - 4
    score += jitter * 0.35

    return max(58.0, min(82.0, score))


# ── legacy cluster vocabulary (still used by Cluster scan) ──────────────

# Legacy constants from the pre-2026-08-07 cluster algorithm (spatial threshold walk
# with gap tolerance). _detect_cluster_zones() was rewritten to a top-N-by-|VEX|
# selection after a ground-truth capture showed the real product's clusters are always
# exactly 3-4 strikes, frequently non-contiguous, with per-strike weights as low as
# 3/100 — none of which a threshold walk can produce. These are retained only because
# MIN_TRIPLE_PEAKS/MIN_QUAD_PEAKS are still the tier boundaries; the band/threshold
# values below are NOT read by the current detector (see CLUSTER_BAND_PCT and
# CLUSTER_RANK_KEY for the live ones) and are kept purely as a record of what was
# tried, since several rounds of tuning were spent on them.
_LEGACY_QUAD_BAND_PCT = 0.07
_LEGACY_QUAD_BAND_DOWN_PCT = 0.07
_LEGACY_ZONE_STRONG_FRAC = 0.34
_LEGACY_ZONE_MAX_GAPS = 2
BATTLE_PCT = 0.01
# Stacked peaks: 3 = triple, 4+ = quad. Still live — these set the tier boundary.
MIN_TRIPLE_PEAKS = 3
MIN_QUAD_PEAKS = 4
MIN_STACK_PEAKS = MIN_TRIPLE_PEAKS

# ── live cluster/scan configuration ──────────────────────────────────────────
# Upper bound on scanner fan-out. This is a shared per-account Alpaca rate budget,
# not a CPU pool — raising it trades scan speed for 429 risk.
SCAN_MAX_WORKERS = 4
# How often (in completed tickers) a partial leaderboard is published to the job.
PARTIAL_EMIT_EVERY = 10
# Strike window fetched for cluster scans. Must stay wider than CLUSTER_BAND_*_PCT or
# the selection runs off the end of the fetched chain.
CLUSTER_DEPTH_PCT = 0.14
# Cluster selection band around spot, and the per-strike metric ranked within it.
# Both fitted against a 36-ticker ground-truth capture of the real product's own
# cluster scan — see _detect_cluster_zones() for the evidence.
CLUSTER_BAND_PCT = 0.10
CLUSTER_BAND_DOWN_PCT = 0.10
CLUSTER_RANK_KEY = "abs_vex"
# 4th strike must be this fraction of the top strike's metric to promote triple -> quad.
QUAD_WEIGHT_FRAC = 0.80

CLUSTER_HINT = (
    "Cluster ranking: quad (4+ peaks) > triple (3 peaks) > battle > golden/walls. "
    "Edit data/weight_lab/cluster_score_weights.json. "
    "GEX is a public-OI heuristic — not verified dealer positioning."
)


def _detect_cluster_zones(profile, spot):
    """Detect cluster zones the way the real product does: the top N strikes by
    |VEX| within a band on each side of spot.

    2026-08-07 rewrite. The previous implementation walked outward from spot
    collecting strikes whose |GEX| cleared a fraction of the side's peak, allowing a
    couple of thin gaps. A ground-truth capture of the real product's own cluster scan
    (data/accessobsidian_scans/, 36 tickers) exposed that as the wrong shape entirely:

      * Its published clusters are ALWAYS exactly 3 or 4 strikes, never more.
      * Those strikes are frequently NOT contiguous (NET: 337.5, 340, 350 - a 10-point
        gap), which a gap-limited walk cannot produce.
      * Their per-strike weights run as low as 3/100 of the cluster's top strike, so no
        "must clear X% of peak" threshold can be selecting them.
      * Every row was 0DTE, i.e. the nearest expiration.

    Fitting selection against those 36 ground-truth strike sets: ranking by |VEX|
    inside a +/-10% band reproduced the exact strike set for 23/36 tickers (87% average
    per-strike overlap) versus 12/36 for |GEX|, 15/36 for open interest. Choosing the
    side by summed |VEX| then gives kind 34/36 and side 34/36 end to end.

    Weight/strength/target semantics are exact, not fitted - confirmed 36/36 on the
    capture: weight_i = 100 * metric_i / max(metric), Strength = sum(weights), and the
    published CLUSTER TARGET is the top-weighted strike.

    VEX beating GEX here is a selection result, not a claim that clusters "are" vanna;
    the two are strongly collinear and the real weights correlate ~0.71 with either.
    """
    if not profile or not spot:
        return []

    zones = []
    for side_name, band_pct in (("above", CLUSTER_BAND_PCT), ("below", CLUSTER_BAND_DOWN_PCT)):
        if side_name == "above":
            banded = [p for p in profile if spot < p["strike"] <= spot * (1 + band_pct)]
        else:
            banded = [p for p in profile if spot > p["strike"] >= spot * (1 - band_pct)]

        ranked = [p for p in banded if (p.get(CLUSTER_RANK_KEY) or 0.0) > 0]
        ranked.sort(key=lambda p: -(p.get(CLUSTER_RANK_KEY) or 0.0))
        top = ranked[:MIN_QUAD_PEAKS]
        if len(top) < MIN_TRIPLE_PEAKS:
            continue

        peak_metric = (top[0].get(CLUSTER_RANK_KEY) or 0.0) or 1.0
        # Promote to a quad only when the 4th strike is nearly as heavy as the top one;
        # the real product's quads are rare (2 of 36) and tightly packed.
        if len(top) >= MIN_QUAD_PEAKS and (top[MIN_QUAD_PEAKS - 1].get(CLUSTER_RANK_KEY) or 0.0) / peak_metric >= QUAD_WEIGHT_FRAC:
            chosen = top[:MIN_QUAD_PEAKS]
        else:
            chosen = top[:MIN_TRIPLE_PEAKS]

        kind = "quad" if len(chosen) >= MIN_QUAD_PEAKS else "triple"
        metric_max = max((p.get(CLUSTER_RANK_KEY) or 0.0) for p in chosen) or 1.0
        weights = [
            {"strike": p["strike"], "weight": round(100.0 * (p.get(CLUSTER_RANK_KEY) or 0.0) / metric_max, 1)}
            for p in chosen
        ]
        strikes_list = [p["strike"] for p in chosen]
        abs_sum = sum(p["abs"] for p in chosen) or 1.0
        # Raw metric sum drives side selection and tier sorting; the normalized
        # weight sum is the number the product displays as "Strength".
        rank_sum = sum((p.get(CLUSTER_RANK_KEY) or 0.0) for p in chosen)
        target_strike = max(chosen, key=lambda p: (p.get(CLUSTER_RANK_KEY) or 0.0))["strike"]

        zones.append({
            "kind": kind,
            "label": (
                f"Quad cluster ({len(chosen)} peaks, {side_name})"
                if kind == "quad"
                else f"Triple cluster ({len(chosen)} peaks, {side_name})"
            ),
            "strikes": strikes_list,
            "levels": weights,
            "low": min(strikes_list),
            "high": max(strikes_list),
            "center": sum(p["strike"] * p["abs"] for p in chosen) / abs_sum,
            "net_gex": sum(p["net"] for p in chosen),
            "strength": rank_sum,
            "strength_norm": round(sum(w["weight"] for w in weights), 1),
            "target_strike": target_strike,
            "oi": sum(p.get("oi") or 0.0 for p in chosen),
            "side": side_name,
            "peak_count": len(chosen),
        })

    return zones


def _detect_multi_exp_clusters(rows, expirations, spot, num_exps=3):
    """Detect clusters using per-expiration zone detection, unioned across
    expirations by side (best/most-peaked zone per side wins).

    Returns zones with an added 'persistence' field (1 to num_exps) counting
    how many individual expirations also show that zone's strikes as part of
    a qualifying zone on the same side.

    2026-08-06: previously re-ran zone detection on a profile with GEX
    *summed* across all fetched expirations. That summing conflates
    unrelated option chains (different DTEs) and was empirically shown, via
    a 36-ticker paired comparison against the real product, to both (a)
    fabricate zones that don't exist in any single expiration (e.g. MSTR
    showed a spurious 4-peak "below" zone from summing, persistence=0,
    while the real product reported a 3-peak "above" zone that IS present
    unchanged in every individual expiration), and (b) silently drop
    legitimate zones entirely for other tickers (e.g. WPM: each individual
    expiration has a clean 6-7 peak "below" zone, but the summed profile's
    relative peak ratios shift enough that none qualify — this ticker only
    matched real data before because classify_setup() falls back to the
    single-expiration zones when extra_zones is an empty list, which is an
    incidental side effect of Python list-truthiness, not an intentional
    fallback). Selecting the best per-expiration zone per side avoids both
    failure modes and measurably improved the paired match rate.
    """
    if not rows or not expirations or not spot:
        return []

    # Detect zones for each expiration separately (prefer nearest-gamma
    # expiration — index 1 — since that's what the rest of the pipeline
    # profiles on; index 0 and 2 only fill in when 1 finds nothing).
    exp_zones = []  # List of (exp_idx, zones) tuples, in preference order
    for exp_idx in [1, 0, 2]:
        if exp_idx >= min(num_exps, len(expirations)):
            continue
        profile = _strike_profile(rows, expiration_index=exp_idx, expirations=expirations)
        zones = _detect_cluster_zones(profile, spot)
        if zones:
            exp_zones.append((exp_idx, zones))

    if not exp_zones:
        return []

    # Track which strikes appear in a qualifying zone in each expiration,
    # for persistence counting.
    strike_exp_count = {}  # rounded strike -> count of expirations where it's in a zone
    for exp_idx, zones in exp_zones:
        for zone in zones:
            for strike in zone.get("strikes", []):
                rounded = round(strike * 2) / 2
                strike_exp_count[rounded] = strike_exp_count.get(rounded, 0) + 1

    # Union: best (most peaks) zone per side across all expirations.
    best_by_side = {}
    for exp_idx, zones in exp_zones:
        for zone in zones:
            side = zone.get("side")
            if side not in best_by_side or zone["peak_count"] > best_by_side[side]["peak_count"]:
                best_by_side[side] = zone

    result_zones = list(best_by_side.values())
    for zone in result_zones:
        zone_strikes = zone.get("strikes", [])
        persistent_count = sum(
            1 for s in zone_strikes
            if strike_exp_count.get(round(s * 2) / 2, 0) >= 2
        )
        zone["persistence"] = persistent_count
        zone["persistence_ratio"] = persistent_count / max(len(zone_strikes), 1)
        if persistent_count >= 2:
            zone["label"] = zone["label"].replace("cluster", f"persistent cluster ({persistent_count} exps)")

    return result_zones


def classify_setup(profile, peaks, summary, spot, extra_zones=None):
    if not profile or not spot or not peaks:
        return [], None
    peak_abs = peaks[0]["abs"] or 1.0
    threshold = peak_abs * PROMINECE_PCT
    golden = next((p for p in peaks if p.get("is_golden")), peaks[0])
    call_wall = summary.get("call_wall_strike")
    put_wall = summary.get("put_wall_strike")
    near = [p for p in peaks if abs(p["strike"] - spot) / spot <= BATTLE_PCT]
    # Zone-based cluster detection (AO-style spatial walk).
    # Replaces the old peak-based approach which missed adjacent high-GEX strikes.
    cluster_zones = _detect_cluster_zones(profile, spot)
    
    # Merge with multi-expiration zones if provided
    if extra_zones:
        # Prefer multi-exp zones (they have persistence info)
        # But keep single-exp zones if multi-exp didn't find anything for that side
        multi_exp_sides = {z.get("side") for z in extra_zones}
        for zone in cluster_zones:
            if zone.get("side") not in multi_exp_sides:
                extra_zones.append(zone)
        cluster_zones = extra_zones
    
    setups = [
        {
            "kind": "golden",
            "label": "Golden / top-pull",
            "strikes": [golden["strike"]],
            "low": golden["strike"],
            "high": golden["strike"],
            "center": golden["strike"],
            "net_gex": golden["net"],
            "strength": golden["abs"],
            "oi": golden.get("oi") or 0.0,
            "side": "above" if golden["strike"] >= spot else "below",
            "peak_count": 1,
        }
    ]
    # Add zone-based clusters (triple/quad) from spatial walk detection.
    for zone in cluster_zones:
        setups.append(zone)
    if near:
        battle = max(near, key=lambda p: p["abs"])
        setups.append(
            {
                "kind": "battle",
                "label": "Battle zone",
                "strikes": [battle["strike"]],
                "low": battle["strike"],
                "high": battle["strike"],
                "center": battle["strike"],
                "net_gex": battle["net"],
                "strength": battle["abs"],
                "oi": battle.get("oi") or 0.0,
                "side": "above" if battle["strike"] >= spot else "below",
                "peak_count": 1,
            }
        )
    if call_wall is not None and call_wall > spot:
        setups.append(
            {
                "kind": "call_wall",
                "label": "Call wall",
                "strikes": [call_wall],
                "low": call_wall,
                "high": call_wall,
                "center": call_wall,
                "net_gex": 0,
                "strength": peak_abs * 0.5,
                "oi": 0.0,
                "side": "above",
                "peak_count": 1,
            }
        )
    if put_wall is not None and put_wall < spot:
        setups.append(
            {
                "kind": "put_floor",
                "label": "Put floor",
                "strikes": [put_wall],
                "low": put_wall,
                "high": put_wall,
                "center": put_wall,
                "net_gex": 0,
                "strength": peak_abs * 0.5,
                "oi": 0.0,
                "side": "below",
                "peak_count": 1,
            }
        )
    priority = {"quad": 0, "triple": 1, "battle": 2, "golden": 3, "call_wall": 4, "put_floor": 5}
    setups.sort(key=lambda s: (priority.get(s["kind"], 9), -s["strength"]))
    return setups, setups[0] if setups else None


def _score_cluster_strategy(setups, *, spot, model, peaks):
    """Weighted Cluster ranking — hard tier then factors (see cluster_score_weights.json)."""
    try:
        import weight_lab

        peak_abs = float((peaks[0]["abs"] if peaks else 0.0) or 1.0)
        payload = weight_lab.score_cluster_pick(
            setups, spot=spot, model=model, peak_abs=peak_abs
        )
        if payload:
            return payload
    except Exception:
        pass
    # Fallback if weight_lab unavailable: coarse kind boosts only.
    kinds = {s.get("kind") for s in setups}
    if "quad" in kinds:
        return {"score": 88.0, "abs_score": 500.0, "score_source": "cluster_fallback", "kind": "quad"}
    if "triple" in kinds:
        return {"score": 78.0, "abs_score": 400.0, "score_source": "cluster_fallback", "kind": "triple"}
    if "battle" in kinds:
        return {"score": 70.0, "abs_score": 300.0, "score_source": "cluster_fallback", "kind": "battle"}
    return {"score": 50.0, "abs_score": 100.0, "score_source": "cluster_fallback", "kind": None}

def _liq_score(model, spot, profile):
    """Liq scan: dominant level far from spot + thin path (audit tooltip)."""
    if not model or not spot:
        return 0.0
    target = model.get("pull_target") or model.get("first_resistance")
    if target is None:
        return 0.0
    dist = abs(target - spot) / spot
    if dist < 0.03:
        return 0.0
    peak_abs = max((p["abs"] for p in profile), default=1.0) or 1.0
    lo, hi = sorted([spot, target])
    path = [p for p in profile if lo < p["strike"] < hi]
    if not path:
        thin = 1.0
    else:
        opposing = sum(1 for p in path if p["abs"] >= peak_abs * 0.25)
        thin = max(0.0, 1.0 - opposing / max(len(path), 1))
    stacked = 1.0 if (model.get("vacuum_count") or 0) >= 2 else 0.4
    tradability = min(sum(p["oi"] for p in profile) / 80000.0, 1.0)
    return 100.0 * (0.40 * min(dist / 0.15, 1.0) + 0.30 * thin + 0.20 * stacked + 0.10 * tradability)


def _signal_geometry(spot, direction, target, invalidation, *, minimum_reward_risk=1.0):
    errors = []
    if not spot or direction not in {"BULLISH", "BEARISH"}:
        return {
            "geometry_valid": False,
            "actionable": False,
            "validation_errors": ["missing_spot_or_direction"],
            "target_distance_pct": None,
            "risk_distance_pct": None,
            "reward_risk": None,
        }
    if target is None:
        errors.append("missing_target")
    if invalidation is None:
        errors.append("missing_invalidation")
    if target is not None:
        if direction == "BULLISH" and target <= spot:
            errors.append("bullish_target_not_above_spot")
        if direction == "BEARISH" and target >= spot:
            errors.append("bearish_target_not_below_spot")
        if abs(target - spot) / spot > 0.12:
            errors.append("target_more_than_12pct_from_spot")
    if invalidation is not None:
        if direction == "BULLISH" and invalidation >= spot:
            errors.append("bullish_invalidation_not_below_spot")
        if direction == "BEARISH" and invalidation <= spot:
            errors.append("bearish_invalidation_not_above_spot")
        if abs(invalidation - spot) / spot > 0.12:
            errors.append("invalidation_more_than_12pct_from_spot")

    target_distance = abs(target - spot) / spot if target is not None else None
    risk_distance = abs(invalidation - spot) / spot if invalidation is not None else None
    reward_risk = (
        target_distance / risk_distance
        if target_distance is not None and risk_distance
        else None
    )
    geometry_valid = not errors
    actionable = bool(
        geometry_valid
        and reward_risk is not None
        and reward_risk >= minimum_reward_risk
    )
    if geometry_valid and not actionable:
        errors.append(f"reward_risk_below_{minimum_reward_risk:.2f}")
    return {
        "geometry_valid": geometry_valid,
        "actionable": actionable,
        "validation_errors": errors,
        "target_distance_pct": target_distance,
        "risk_distance_pct": risk_distance,
        "reward_risk": reward_risk,
    }


def _flash_gamma_regime(profile, spot):
    """Classify near-spot gamma as 'pin' (net-positive, dealers dampen movement) or
    'mixed' (balanced/negative, two-sided). Standard dealer-gamma heuristic: strongly
    net-positive GEX near spot implies dealer hedging that pins price; balanced or
    negative implies no such dampening.
    """
    if not profile or not spot:
        return "mixed"
    # Read the profile defensively. _strike_profile() always supplies net/abs, but
    # this helper is reachable with hand-built or partial profiles (the scanner
    # safety tests pass one with only strike/abs), and a KeyError here propagates
    # out of _flash_components and fails the whole ticker rather than degrading.
    near = [p for p in profile if p.get("strike") is not None and abs(p["strike"] - spot) / spot <= 0.02]
    if not near:
        return "mixed"
    net_sum = sum(float(p.get("net") or 0.0) for p in near)
    abs_sum = sum(float(p.get("abs") or 0.0) for p in near) or 1.0
    return "pin" if (net_sum / abs_sum) >= 0.35 else "mixed"


def _flash_vwap(ticker, spot, bars_fn):
    """Session VWAP from today's intraday bars, if a bars_fn was supplied."""
    if bars_fn is None or not spot:
        return None, None
    try:
        payload = bars_fn(ticker, "5m", limit=150)
        all_bars = payload.get("bars") or []
        if not all_bars:
            return None, None
        # Anchor to the most recent bar's date rather than UTC "today" — after-hours,
        # UTC has already rolled to the next calendar day while the latest session's
        # bars are still dated the prior UTC date (same boundary issue as expirations
        # in matrix() above).
        session_date = str(all_bars[-1].get("time") or "")[:10]
        todays_bars = [b for b in all_bars if str(b.get("time") or "")[:10] == session_date]
        if not todays_bars:
            return None, None
        pv_sum = 0.0
        vol_sum = 0.0
        for b in todays_bars:
            hi, lo, close, vol = b.get("high"), b.get("low"), b.get("close"), b.get("volume") or 0
            if hi is None or lo is None or close is None or vol <= 0:
                continue
            pv_sum += ((hi + lo + close) / 3.0) * vol
            vol_sum += vol
        if vol_sum <= 0:
            return None, None
        vwap = pv_sum / vol_sum
        return round(vwap, 4), ("above" if spot >= vwap else "below")
    except Exception:
        return None, None


def _flash_dte(expirations):
    """Days to the nearest fetched expiration (flash always profiles the nearest one)."""
    if not expirations:
        return None
    label = expirations[0] if isinstance(expirations[0], str) else (expirations[0].get("expiration") or expirations[0].get("date"))
    if not label:
        return None
    try:
        exp_date = datetime.strptime(str(label)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    return max(0, (exp_date - today).days)


def _edge_local(summary):
    """Conviction 0-100 from Obsidian detector state. Local metric, not the real
    product's Edge — see the note at its call site."""
    if not summary:
        return None
    score = 50.0
    # A deeper collapse off a significant run is a stronger structural signal.
    if summary.get("significant"):
        score += 15.0
    score += 25.0 * float(summary.get("collapse_pct") or 0.0)
    # Trend agreement with the momentum bias.
    bias = summary.get("bias")
    if (bias == "BULLISH" and summary.get("trend_up")) or (bias == "BEARISH" and summary.get("trend_down")):
        score += 10.0
    # A live coil is compression, not yet conviction.
    if summary.get("coiling"):
        score -= 5.0
    return round(max(0.0, min(100.0, score)), 1)


def _obsidian_state(ticker, bars_fn, timeframe="5m", limit=300):
    """Run the Obsidian EOD detector for a ticker and shape it for a Flash card.

    Returns None when bars are unavailable — the caller keeps its GEX-only view
    rather than inventing a momentum narrative it cannot support.
    """
    if bars_fn is None or not ticker:
        return None
    try:
        from . import obsidian_eod
    except ImportError:
        import obsidian_eod
    try:
        payload = bars_fn(ticker, timeframe, limit=limit)
        bars = (payload or {}).get("bars") or []
        if len(bars) < 60:
            return None
        # Full Session: the scanner runs all day, so gating signals to the final
        # 30 minutes would leave every card blank outside that window. The EOD
        # window is still reported via `in_window`/`hot` for callers that care.
        states, summary = obsidian_eod.compute(bars, {"mode": "Full Session"})
        if not states:
            return None
        events = obsidian_eod.timeline(states, limit=5, bar_minutes=5)
        latest = summary.get("latest_event") or ""
        setup_label = summary.get("setup_label") or ""
        setup_name = summary.get("setup") or ""
        return {
            "setup": setup_label,
            "setup_family": setup_name.lower().replace(" ", "_") if setup_name else "",
            "setup_id": summary.get("setup_id"),
            "event_timeline": events,
            "latest_event": latest,
            "coiling": bool(summary.get("coiling")),
            "momentum_bias": summary.get("bias"),
            "collapse_pct": summary.get("collapse_pct"),
            "significant": bool(summary.get("significant")),
            "eod_in_window": bool(summary.get("in_window")),
            "eod_hot": bool(summary.get("hot")),
            "trend_up": bool(summary.get("trend_up")),
            "trend_down": bool(summary.get("trend_down")),
            # Local conviction composite. The real product shows an "Edge" number
            # (observed 91-106) that is CONSTANT for the life of a setup episode —
            # 27 of 30 captured (ticker, setup, entry) episodes carried a single
            # value — so it is computed once at surface time. 30 episodes was not
            # enough to recover the actual formula, so this is deliberately a
            # DIFFERENT field name and is surfaced as "Edge*" with a disclosure:
            # it is our own composite over signals we can defend (collapse depth,
            # participation, trend agreement), NOT a reproduction of their Edge.
            "edge_local": _edge_local(summary),
            "entry_price": summary.get("entry_price"),
            "entry_bars_ago": summary.get("entry_bars_ago"),
        }
    except Exception:
        return None


def _flash_components(model, spot, profile, day_change_pct, *, ticker=None, expirations=None, bars_fn=None):
    """Intraday Flash score components from nearest-exp surface (audit weights)."""
    if not model or not spot or not profile:
        return None
    peak_abs = max((p["abs"] for p in profile), default=1.0) or 1.0
    peak_vex = max((p.get("abs_vex") or 0.0 for p in profile), default=1.0) or 1.0
    floor = model.get("first_support") or model.get("put_wall")
    ceil = model.get("first_resistance") or model.get("call_wall")
    pull = model.get("pull_target") or ceil or floor

    def _level_strength(level, use_vex=False):
        if level is None:
            return 0.0
        nearest = min(profile, key=lambda p: abs(p["strike"] - level))
        key = "abs_vex" if use_vex else "abs"
        return min((nearest.get(key) or 0.0) / (peak_vex if use_vex else peak_abs), 1.0)

    gex_fc = 0.5 * (_level_strength(floor) + _level_strength(ceil))
    vex_fc = 0.5 * (_level_strength(floor, True) + _level_strength(ceil, True))

    # Session key-level proxy: proximity of spot to nearest peak (PMH/PDH stand-in).
    nearest_peak = min(profile, key=lambda p: abs(p["strike"] - spot))
    session_align = max(0.0, 1.0 - abs(nearest_peak["strike"] - spot) / (spot * 0.02))

    # Volume-profile proxy from option volume at strikes near spot.
    near = [p for p in profile if abs(p["strike"] - spot) / spot <= 0.03]
    vol_sum = sum(p.get("volume") or 0 for p in near) or 1.0
    poc = max(near or profile, key=lambda p: p.get("volume") or 0)
    vp_align = max(0.0, 1.0 - abs(poc["strike"] - spot) / (spot * 0.025))

    chg = abs(float(day_change_pct or 0.0))
    momentum = min(chg / 2.5, 1.0)
    # VWAP proxy: mean-reversion vs day move (mild positive if not extended).
    vwap_pos = max(0.0, 1.0 - chg / 4.0)

    touch = session_align
    # Flow proxy: OI concentration near spot.
    oi_near = sum(p.get("oi") or 0 for p in near)
    oi_all = sum(p.get("oi") or 0 for p in profile) or 1.0
    flow5 = min(oi_near / max(oi_all * 0.15, 1.0), 1.0)

    # ATR-scaled first target quality: pull within ~0.5–2.5% of spot.
    atr_quality = 0.0
    if pull is not None:
        dist = abs(pull - spot) / spot
        if 0.004 <= dist <= 0.035:
            atr_quality = 1.0 - abs(dist - 0.015) / 0.02
            atr_quality = max(0.0, min(1.0, atr_quality))

    # Preserve the original relative weights, but normalize by their 1.10 sum.
    # Without this denominator, high-quality cards mechanically saturate at 99.
    weighted_components = (
        0.18 * gex_fc
        + 0.14 * vex_fc
        + 0.12 * session_align
        + 0.12 * vp_align
        + 0.12 * vwap_pos
        + 0.12 * momentum
        + 0.10 * touch
        + 0.10 * flow5
        + 0.10 * atr_quality
    )
    score = 100.0 * weighted_components / 1.10
    # Directional first targets (ATR rungs) toward pull.
    atr = spot * 0.008
    direction = model.get("direction") or "NEUTRAL"
    if direction == "BEARISH":
        t1, t2 = spot - atr, spot - 2 * atr
    else:
        t1, t2 = spot + atr, spot + 2 * atr
    stretch = pull
    invalidation = model.get("close_under") if direction == "BULLISH" else model.get("reclaim")
    geometry = _signal_geometry(
        spot,
        direction,
        t1,
        invalidation,
        minimum_reward_risk=1.0,
    )

    # Agentic state machine from distance-to-trigger.
    trigger = floor if direction == "BULLISH" else ceil
    if trigger is None:
        trigger = pull
    state = "dormant"
    if trigger is not None:
        dist_pct = abs(spot - trigger) / spot
        if dist_pct <= 0.0025:
            state = "triggered"
        elif dist_pct <= 0.008:
            state = "arming"
        if stretch is not None and abs(spot - stretch) / spot <= 0.004:
            state = "target_1_hit"
        if stretch is not None and (
            (direction != "BEARISH" and spot >= stretch) or (direction == "BEARISH" and spot <= stretch)
        ):
            state = "completed"

    trigger_kind = "floor" if direction == "BULLISH" else "ceiling"
    stretch_kind = "ceiling" if direction == "BULLISH" else "floor"
    trigger_dist = abs(spot - trigger) / spot if trigger is not None else None
    trigger_proximity = (
        "at" if trigger_dist is not None and trigger_dist <= 0.0025
        else "nearing" if trigger_dist is not None and trigger_dist <= 0.008
        else None
    )

    regime = _flash_gamma_regime(profile, spot)
    vwap, vwap_side = _flash_vwap(ticker, spot, bars_fn)
    dte = _flash_dte(expirations)

    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "targets": [round(t1, 2), round(t2, 2)],
        "first_target": round(t1, 2),
        "push_target": round(t2, 2),
        "stretch": stretch,
        "invalidation": invalidation,
        "agent_state": state,
        "trigger": trigger,
        "trigger_kind": trigger_kind,
        "trigger_proximity": trigger_proximity,
        "stretch_kind": stretch_kind,
        "regime": regime,
        "vwap": vwap,
        "vwap_side": vwap_side,
        "dte": dte,
        **geometry,
        "components": {
            "gex_fc": round(gex_fc, 3),
            "vex_fc": round(vex_fc, 3),
            "session": round(session_align, 3),
            "vp": round(vp_align, 3),
            "vwap": round(vwap_pos, 3),
            "momentum": round(momentum, 3),
            "touch": round(touch, 3),
            "flow": round(flow5, 3),
            "atr": round(atr_quality, 3),
        },
    }


def _cluster_strength(primary, profile):
    """Displayed cluster Strength: 100 x sum(|GEX_i| / chain_peak_|GEX|) over the
    cluster's strikes.

    2026-08-06: replaces the previous value, which was weight_lab's abs_score — a
    tier-offset sort key ((n_tiers - tier_index) * tier_gap + kind_boost + factors).
    That number correlated with the real product's published Strength at only ~0.19
    (noise) across a 36-ticker paired sample, and its tier flooring made every triple
    land near ~250 while real triples span 72-260.

    This per-strike normalized sum correlates at ~0.68 on the same sample and is
    structurally right: each strike contributes at most 1.0 (100 points), so a triple
    tops out near 300 and a quad near 400 — matching the observed real ranges
    (triples 72-260, quads 254-304). Absolute calibration is still imperfect (a linear
    fit suggested real ~= 0.34*this + 122, MAE ~32), but that fit is 2 params on 22
    points, so it is deliberately NOT applied here — an earlier calibration in this
    same effort was fit on 6 tickers and failed to generalize at 36.
    """
    if not primary:
        return None
    # Precomputed in _detect_cluster_zones against the zone's own expiration profile.
    norm = primary.get("strength_norm")
    if norm is not None:
        return norm
    # Fallback for non-zone setups (golden/battle/walls), which carry a raw |GEX|.
    if not profile:
        return None
    chain_peak = max((p["abs"] for p in profile), default=0.0)
    if chain_peak <= 0:
        return None
    return round(100.0 * float(primary.get("strength") or 0.0) / chain_peak, 1)


def analyze_ticker(matrix_fn, ticker, feed, mode, strategy, cluster_exp=None, bars_fn=None):
    cfg = MODE_CONFIG.get(mode, MODE_CONFIG["short"])
    # Flash always uses nearest expiration depth.
    flash_mode = strategy in {"flash", "flash_index", "flash_agentic"}
    # Setup Scanner: always nearest (lowest) DTE — 1 expiration.
    # Explicit ISO date (backtest / research) fetches enough columns to locate that exp.
    # Cluster strategy needs gamma data, so skip 0DTE (no gamma) and use 2nd expiration.
    if cluster_exp and cluster_exp not in {"nearest", "", "all", None}:
        exp_count = max(int(cfg.get("expirations") or 1), 6)
        profile_exp = cluster_exp
    elif strategy == "cluster":
        # Fetch 3 expirations (the multi-exp union in _detect_multi_exp_clusters needs
        # them) but profile the NEAREST one.
        #
        # 2026-08-06: this was index 1, on the theory that index 0 is a gamma-less 0DTE.
        # That theory was wrong on two counts. (a) matrix() now filters out already-
        # expired contracts, so index 0 is a live expiration, not a dead 0DTE. (b) A
        # paired test against the real product's published CLUSTER TARGET strike on 36
        # tickers matched index 0 roughly twice as often as index 1 (22/36 vs 10/36),
        # and the cluster-strength correlation is likewise materially higher on index 0.
        # The real product is clearly reading the nearest expiration.
        # Kept at 3 even though only index 0 is profiled. Dropping to 1 is a plausible
        # speedup (it narrows the snapshot window), but it could NOT be validated: by
        # the time it was tried the 36-ticker ground-truth capture had gone stale —
        # the market had reopened and spot had drifted a mean of 1.8% (max 6.4%,
        # 19/31 tickers over 1%), which moves the clusters themselves. Both the "with"
        # and "without" measurements were therefore meaningless. Holding at 3, the
        # value that measured kind 31/36 + side 31/36 against a same-moment capture.
        # Retest against a fresh paired capture before changing.
        exp_count = 3
        # 0DTE. The ground-truth capture of the real product's cluster scan reported
        # "0DTE" on all 36 rows, and fitting strike selection confirms it: exact
        # strike-set matches were 23/36 on expiration index 0 versus 6/36 on index 1.
        # This was previously index 1 because Alpaca publishes no gamma for same-day
        # contracts, leaving index 0 an all-zero column — resolve_iv()/model_gamma()
        # in exposure.py now reconstruct it, so index 0 is usable.
        profile_exp = 0
    else:
        exp_count = 1
        profile_exp = "nearest"

    # Cluster scans need a wider strike window than the default 6%: on a 36-ticker
    # paired comparison, 8 of the real product's published CLUSTER TARGET strikes
    # (ENPH +7.3%, APA +7.9%, INTC +7.8%, STM -7.1%, KGC -6.8%, ONON +7.1%,
    # APLD +6.5%, BP +6.6%) sat OUTSIDE ±6% and so were never even fetched — those
    # tickers could not possibly be matched, and showed up as "missing" locally.
    # 12% covers every observed real target with margin.
    if flash_mode:
        depth = 0.06
    elif strategy == "cluster":
        depth = CLUSTER_DEPTH_PCT
    else:
        depth = cfg["depth"]

    payload = matrix_fn(
        ticker,
        feed,
        depth,
        exp_count,
        force=False,
        chain_pages=cfg.get("pages", 2),
    )
    spot = (payload.get("quote") or {}).get("price_context")
    summary = payload.get("summary") or {}
    expirations = payload.get("expirations") or []
    profile = _strike_profile(
        payload.get("rows"),
        cluster_exp=profile_exp,
        expirations=expirations,
    )
    peaks = _local_peaks(profile)
    model = cipher_model_from_profile(ticker, profile, peaks, summary, spot)
    
    # Clusters come from the 0DTE profile alone. The multi-expiration union that used
    # to run here contradicted the ground-truth capture (every real cluster row is
    # 0DTE), and unioning a later expiration's zone in would replace the correct
    # 0DTE cluster with one the real product never reports. _detect_multi_exp_clusters
    # is retained for callers that specifically want persistence across expirations.
    setups, primary = classify_setup(profile, peaks, summary, spot)
    
    day_chg = (payload.get("quote") or {}).get("day_change_pct")

    if not model:
        # Cluster detection now ranks by |VEX|, but qualification still went through
        # cipher_model_from_profile(), which needs prominent |GEX| peaks. A name with
        # a flat/thin GEX surface but a clean VEX cluster was therefore dropped
        # entirely: U, TMUS and TPR each produced 2 valid zones yet returned
        # score=0/supports=None and never reached the results list, showing up as
        # "missing" against the real product's own scan. Fall back to a minimal
        # model built from the zones so the cluster path no longer depends on a
        # signal it stopped using.
        cluster_zones = _detect_cluster_zones(profile, spot) if strategy == "cluster" else []
        if not cluster_zones:
            return {
                "ticker": ticker,
                "spot": spot,
                "score": 0.0,
                "abs_score": 0.0,
                "direction": "NEUTRAL",
                "setup_kind": None,
            }
        primary_zone = sorted(
            cluster_zones, key=lambda z: (0 if z["kind"] == "quad" else 1, -z["strength"])
        )[0]
        above = primary_zone["side"] == "above"
        model = {
            "score": 50.0,
            "direction": "BULLISH" if above else "BEARISH",
            "supports": [] if above else primary_zone["strikes"],
            "resistances": primary_zone["strikes"] if above else [],
            "pull_target": primary_zone.get("target_strike") or primary_zone["center"],
            "vacuum_targets": [],
            "vacuum_count": 0,
            "close_under": None,
            "reclaim": None,
            "first_support": (min(primary_zone["strikes"]) if not above else None),
            "first_resistance": (min(primary_zone["strikes"]) if above else None),
            "last_vacuum_target": None,
            "support_count": 0 if above else len(primary_zone["strikes"]),
            "resistance_count": len(primary_zone["strikes"]) if above else 0,
            "golden": primary_zone.get("target_strike"),
            "call_wall": None,
            "put_wall": None,
            "gamma_flip": None,
            "read": (
                f"{primary_zone['label']} — VEX-derived cluster; the GEX surface was too "
                "flat to fit the Cipher model, so structural levels are cluster-only."
            ),
        }
        setups, primary = cluster_zones, primary_zone

    score = model["score"]
    flash = None
    agent_state = None
    score_source = None
    cluster_strength = None
    if strategy == "liquidity":
        score = round(_liq_score(model, spot, profile), 1)
    elif strategy == "cluster":
        cluster_score = _score_cluster_strategy(setups, spot=spot, model=model, peaks=peaks)
        score = float(cluster_score.get("score") or 0.0)
        score_source = cluster_score.get("score_source") or "cluster_weights"
        # Keep abs_score for hard-tier sort (quad always above triple).
        model_abs = float(cluster_score.get("abs_score") or score)
        cluster_strength = _cluster_strength(primary, profile)
        # Add GEX forecast for cluster strikes (if history available).
        # GEX is a public-OI heuristic — not verified dealer positioning.
        if primary and primary.get("strikes"):
            try:
                from . import gex_forecast
                evolution = gex_forecast.predict_cluster_evolution(ticker, primary, spot)
                if evolution:
                    primary["gex_forecast"] = evolution
            except Exception:
                pass  # Forecast is optional enhancement
    elif flash_mode:
        flash = _flash_components(model, spot, profile, day_chg, ticker=ticker, expirations=expirations, bars_fn=bars_fn)
        if flash:
            # Momentum-structure layer from the Obsidian EOD detector (the original
            # TradingView indicator this product's intraday engine is built on).
            # It supplies the setup name, the event timeline, and the coil/release
            # state that the real product's Flash Agentic cards are built from —
            # previously those fields were absent and the cards had no narrative.
            obs = _obsidian_state(ticker, bars_fn)
            if obs:
                flash.update(obs)
                # Progress toward the first target, anchored on the price at which the
                # setup surfaced (not the pivot). Formula taken from the real product's
                # own cards: clamp((spot - entry) / (target - entry), 0, 1) matched 118
                # of 122 captured values to within 4 points.
                # Regime has three states in the real product — Pin / Trend / Mixed —
                # but only Pin and Mixed were ever emitted here, so a decisively
                # trending tape was mislabelled "Mixed". Gamma pinning still wins
                # (dealers dampening price is the dominant regime), otherwise a
                # decisive EMA slope from the Obsidian trend context is "Trend".
                if flash.get("regime") != "pin":
                    if obs.get("trend_up") or obs.get("trend_down"):
                        flash["regime"] = "trend"

                # Episode tracking with target extension. The card's target used to
                # be recomputed from live spot on every scan, so it retreated as
                # price advanced and was never actually reached. An episode anchors
                # the target, promotes to the next structural level once price
                # trades through it, and closes on a structure flip — the behaviour
                # the product describes on its own Micron walkthrough.
                structure_flipped = any(
                    "flipped" in str(event).lower() for event in (obs.get("events") or [])
                )
                walls = sorted(
                    {float(p["strike"]) for p in (profile or []) if p.get("strike") is not None}
                )
                episode = agentic_episodes.update(
                    ticker,
                    direction=flash.get("direction") or model.get("direction"),
                    setup=obs.get("setup") or flash.get("setup"),
                    spot=spot,
                    first_target=flash.get("first_target"),
                    invalidation=flash.get("invalidation"),
                    levels=walls,
                    structure_flipped=structure_flipped,
                )
                if episode:
                    flash["episode"] = {
                        "entry_price": episode["entry_price"],
                        "original_target": episode["original_target"],
                        "target": episode["target"],
                        "extension_count": episode["extension_count"],
                        "extensions": episode["extensions"][-4:],
                        "max_favorable": episode["max_favorable"],
                        "move_pct": episode["move_pct"],
                        "state": episode["state"],
                        "close_reason": episode["close_reason"],
                        "opened_at": episode["opened_at"],
                    }
                    # The anchored target is the one the card should show.
                    flash["first_target"] = episode["target"]
                    if episode.get("progress_pct") is not None:
                        flash["target_progress_pct"] = episode["progress_pct"]
                        flash["target_progress"] = f"{int(round(episode['progress_pct']))}% to target"
                    if episode["extension_count"]:
                        flash["target_extended"] = True
                else:
                    entry = obs.get("entry_price")
                    tgt = flash.get("first_target")
                    if entry is not None and tgt is not None and spot is not None and tgt != entry:
                        pct = (spot - entry) / (tgt - entry) * 100.0
                        pct = max(0.0, min(100.0, pct))
                        flash["target_progress_pct"] = round(pct, 1)
                        flash["target_progress"] = f"{int(round(pct))}% to target"
            clarity = None
            comps = flash.get("components") or {}
            # Proxy runway clarity from thin path + ATR quality (0–1).
            clarity = 0.55 * float(comps.get("atr") or 0) + 0.45 * float(
                (model.get("vacuum_count") or 0) / 3.0
            )
            clarity = max(0.0, min(1.0, clarity))
            fitted_flash = _try_fitted_flash_score(model, profile, spot, runway_clarity=clarity, dte=5.0)
            if fitted_flash is not None:
                score = round(max(0.0, min(100.0, float(fitted_flash))), 1)
                flash["score"] = score
                flash["score_source"] = "fitted_weights"
            else:
                score = flash["score"]
                flash["score_source"] = "heuristic"
            agent_state = flash["agent_state"]
            regime_label = "Long-gamma pin" if flash.get("regime") == "pin" else "Mixed gamma"
            vwap_clause = (
                f" price {flash['vwap_side']} VWAP." if flash.get("vwap_side") else ""
            )
            model = {
                **model,
                "vacuum_targets": flash["targets"],
                "pull_target": flash.get("stretch") or model.get("pull_target"),
                "read": (
                    f"Flash {agent_state}: trigger { _fmt_level(flash.get('trigger')) } · "
                    f"T1/T2 {', '.join(_fmt_level(t) for t in flash['targets'])} · "
                    f"stretch {_fmt_level(flash.get('stretch'))}. "
                    f"{model.get('read') or ''} "
                    f"{regime_label} regime,{vwap_clause}"
                ).strip(),
            }

    direction = model["direction"]
    target = flash.get("first_target") if flash else model.get("pull_target")
    # Cluster scans publish the cluster's own target strike (highest-OI strike in the
    # zone) rather than the generic model pull target — see _detect_cluster_zones().
    if strategy == "cluster" and primary and primary.get("target_strike") is not None:
        target = primary["target_strike"]
    invalidation = (
        flash.get("invalidation")
        if flash
        else (model.get("close_under") if direction == "BULLISH" else model.get("reclaim"))
    )
    geometry = (
        {
            key: flash.get(key)
            for key in (
                "geometry_valid",
                "actionable",
                "validation_errors",
                "target_distance_pct",
                "risk_distance_pct",
                "reward_risk",
            )
        }
        if flash
        else _signal_geometry(
            spot,
            direction,
            target,
            invalidation,
            minimum_reward_risk=0.0,
        )
    )
    setup_type = (
        (primary or {}).get("label")
        or (primary or {}).get("kind")
        or ("FLASH" if flash_mode else ("CLUSTER" if strategy == "cluster" else "CIPHER MODEL"))
    )

    return {
        "ticker": ticker,
        "spot": spot,
        "day_change_pct": day_chg,
        "score": score,
        "strength": cluster_strength if strategy == "cluster" else None,
        "abs_score": model_abs if strategy == "cluster" else score,
        "direction": direction,
        "state": agent_state.upper() if agent_state else "",
        "setup_type": str(setup_type).upper(),
        "target": target,
        "invalidation": invalidation,
        "geometry_valid": geometry.get("geometry_valid"),
        "actionable": geometry.get("actionable"),
        "validation_errors": geometry.get("validation_errors") or [],
        "target_distance_pct": geometry.get("target_distance_pct"),
        "risk_distance_pct": geometry.get("risk_distance_pct"),
        "reward_risk": geometry.get("reward_risk"),
        "reason": model["read"],
        "read": model["read"],
        "supports": model["supports"],
        "resistances": model["resistances"],
        "pull_target": model["pull_target"],
        "vacuum_targets": model["vacuum_targets"],
        "close_under": model["close_under"],
        "reclaim": model.get("reclaim"),
        "first_support": model["first_support"],
        "first_resistance": model["first_resistance"],
        "last_vacuum_target": model["last_vacuum_target"],
        "support_count": model["support_count"],
        "resistance_count": model["resistance_count"],
        "vacuum_count": model["vacuum_count"],
        "setup_kind": "flash" if flash_mode else ("cluster" if strategy == "cluster" else "cipher_model"),
        "level": model["pull_target"],
        "golden": model["golden"],
        "call_wall": model["call_wall"],
        "put_wall": model["put_wall"],
        "gamma_flip": model["gamma_flip"],
        "cluster": primary,
        "setups": setups,
        "peak_count": len(peaks),
        "coverage_cells": (payload.get("coverage") or {}).get("calculated_cells"),
        "contracts": (payload.get("coverage") or {}).get("contracts"),
        "feed": payload.get("feed"),
        "mode": mode,
        "strategy": strategy,
        "mode_label": cfg["label"],
        "agent_state": agent_state,
        "flash": flash,
        "cluster_exp": cluster_exp,
        "score_source": score_source,
    }


def build_cluster_groups(picks):
    order = [
        ("quad", "Quad clusters", "4+ stacked peaks above spot"),
        ("triple", "Triple clusters", "3 stacked peaks above spot"),
        ("battle", "Battle zones", "Peak glued to spot"),
        ("golden", "Golden / top-pull", "Global |GEX| magnet"),
        ("call_wall", "Call walls", "Upside dealer wall"),
        ("put_floor", "Put floors", "Downside cushion"),
    ]
    buckets = {key: [] for key, _, _ in order}
    for pick in picks:
        chosen = None
        for kind, _, _ in order:
            match = next((s for s in (pick.get("setups") or []) if s.get("kind") == kind), None)
            if match:
                chosen = match
                break
        if not chosen:
            continue
        kind = chosen.get("kind")
        if kind not in buckets:
            continue
        buckets[kind].append(
            {
                "ticker": pick["ticker"],
                "score": pick.get("score"),
                "spot": pick.get("spot"),
                "low": chosen.get("low"),
                "high": chosen.get("high"),
                "center": chosen.get("center"),
                "strikes": chosen.get("strikes") or [],
                "label": chosen.get("label"),
                "direction": pick.get("direction"),
                "peak_count": chosen.get("peak_count"),
                "score_source": pick.get("score_source"),
            }
        )
    out = []
    for kind, title, blurb in order:
        items = sorted(buckets[kind], key=lambda x: x.get("score") or 0, reverse=True)
        if not items:
            continue
        out.append(
            {
                "id": kind,
                "kind": kind,
                "title": title,
                "blurb": blurb,
                "count": len(items),
                "tickers": [i["ticker"] for i in items[:12]],
                "members": items[:12],
            }
        )
    return out


def _update_job(job_id, **kwargs):
    with _JOB_LOCK:
        job = _SCAN_JOBS.get(job_id)
        if not job:
            return
        job.update(kwargs)


def _apply_strategy_filter(results, strategy):
    """Strategy-specific qualification. Split out of run_scan so the same rules can be
    applied to the partial result set mid-scan for incremental streaming."""
    if strategy == "cluster":
        return [
            r for r in results
            if any(s.get("kind") in {"quad", "triple", "battle"} for s in (r.get("setups") or []))
        ]
    if strategy == "liquidity":
        return [r for r in results if (r.get("score") or 0) >= 40]
    if strategy == "flash_agentic":
        # Surface armed/triggered plus completed (UI greys finished plays).
        return [
            r for r in results
            if r.get("agent_state") in {"arming", "triggered", "target_1_hit", "target_2_hit", "completed"}
            and (r.get("score") or 0) >= 50
            and r.get("actionable") is True
        ]
    if strategy in {"flash", "flash_index"}:
        return [
            r for r in results
            if (r.get("score") or 0) >= 48 and r.get("geometry_valid") is True
        ]
    return results


_CLUSTER_TIER_ORDER = {"quad": 0, "triple": 1, "battle": 2, "golden": 3, "call_wall": 4, "put_floor": 5}


def _cluster_sort_key(item):
    """Tier first, then displayed Strength descending — the real product's ordering.

    2026-08-07: cluster results used to be ranked by weight_lab's `abs_score`, which
    is tier_gap-dominated and therefore almost constant inside a tier: a full-universe
    scan produced 260.7, 260.6, 260.5, 260.3, 260.2, 260.1, 260.0, 259.9 … for every
    triple. With the tier term cancelling out, the order was decided by the tiny
    factor remainder, and those factors (oi_log especially) scale with liquidity — so
    the same mega-caps surfaced on every single scan (SPY, QQQ, HYG, GLD, XLF, TSLA,
    NVDA, MSFT, GOOGL …) regardless of how good their clusters actually were, while
    genuinely stronger setups on smaller names never made the top 30.

    `strength_norm` (sum of the per-strike weights, 0-400) is scale-free and spreads
    properly — 369, 234, 216, 209, 194 … across that same scan — and matches how the
    real product's own published list is ordered: quads first, then triples by
    descending Strength.
    """
    setup = item.get("cluster") or {}
    kind = (setup.get("kind") or "").lower()
    tier = _CLUSTER_TIER_ORDER.get(kind, 9)
    strength = item.get("strength")
    if strength is None:
        strength = setup.get("strength_norm") or 0.0
    return (tier, -float(strength))


def _rank_and_slice(results, limit, strategy=None):
    if strategy == "cluster":
        ranked = sorted(results, key=_cluster_sort_key)
    else:
        ranked = sorted(results, key=lambda item: item.get("abs_score") or 0.0, reverse=True)
    top = ranked[: max(1, min(int(limit), 50))]
    # Clear ranks before re-assigning. This function now runs repeatedly during a
    # scan to publish partial leaderboards, and it mutates the shared result dicts —
    # so a ticker that made an early partial top but not the final one kept a stale
    # `rank`, which then leaked into `clusters` (built from ranked[:40]).
    for item in ranked:
        if "rank" in item:
            item.pop("rank", None)
    for idx, item in enumerate(top, start=1):
        item["rank"] = idx
    return ranked, top


def run_scan(
    matrix_fn,
    *,
    mode="short",
    strategy="cipher",
    feed="opra",
    limit=30,
    universe=None,
    workers=1,
    cache_seconds=90,
    job_id=None,
    progress_cb=None,
    cluster_exp=None,
    bars_fn=None,
):
    mode = mode if mode in MODE_CONFIG else "short"
    strategy = (
        strategy
        if strategy in {"cipher", "standard", "cluster", "liquidity", "flash", "flash_index", "flash_agentic"}
        else "cipher"
    )
    if strategy == "standard":
        strategy = "cipher"


    if strategy == "flash":
        universe = list(FLASH_UNIVERSE)
        mode = "short"
    elif strategy == "flash_index":
        universe = list(FLASH_INDEX_UNIVERSE)
        mode = "short"
    elif strategy == "flash_agentic":
        universe = list(FLASH_UNIVERSE)
        mode = "short"

    # Setup Scanner always uses nearest (lowest) DTE unless an explicit ISO date is passed.
    if not cluster_exp or cluster_exp in {"", "all"}:
        cluster_exp = "nearest"

    tickers = []
    seen = set()
    for t in universe or SCAN_UNIVERSE:
        t = t.upper()
        if t not in seen:
            seen.add(t)
            tickers.append(t)

    cache_key = (mode, strategy, feed, tuple(tickers), int(limit), cluster_exp or "nearest", "v10-nearest-1exp")
    cached = _SCAN_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < cache_seconds and not job_id:
        return deepcopy(cached[1])

    started = time.time()
    results, errors = [], []
    attempted = len(tickers)
    done = 0

    def _report():
        pct = int(100 * done / max(attempted, 1))
        if progress_cb:
            progress_cb(done, attempted, pct)
        if job_id:
            update = {
                "status": "running",
                "done": done,
                "total": attempted,
                "pct": pct,
                "message": f"Scanning the universe… {done}/{attempted} tickers ({pct}%)",
            }
            # Publish a running leaderboard so the UI can fill in cards while a
            # full-universe scan is still working, instead of showing a bare
            # progress bar for minutes and then everything at once. Recomputed on
            # an interval rather than every ticker — ranking the partial set on all
            # 580 completions would cost more than it saves.
            if done % PARTIAL_EMIT_EVERY == 0 or done == attempted:
                try:
                    _, partial_top = _rank_and_slice(
                        _apply_strategy_filter(list(results), strategy), limit, strategy
                    )
                    update["partial_top"] = deepcopy(partial_top)
                except Exception:
                    pass  # never let the preview break the scan
            _update_job(job_id, **update)

    _report()
    # Bounded fan-out. This was strictly serial because an earlier unbounded fan-out
    # tripped Alpaca 429s. Two things changed: option_chain() now requests only the
    # expiration window it needs (SPY dropped from 15 pages/14.5k contracts to 5
    # pages/4k), and alpaca()/option_open_interest() retry 429 with Retry-After-aware
    # backoff instead of failing the ticker outright. A small pool is therefore safe
    # and turns a ~26 minute 580-ticker cluster scan into a few minutes. Kept
    # deliberately low — this is a shared per-account rate budget, not a CPU pool.
    worker_count = max(1, min(int(workers or 1), SCAN_MAX_WORKERS))

    def _analyze(ticker):
        return ticker, analyze_ticker(
            matrix_fn, ticker, feed, mode, strategy, cluster_exp, bars_fn=bars_fn
        )

    if worker_count == 1:
        for ticker in tickers:
            try:
                _, item = _analyze(ticker)
                if item.get("score", 0) >= 45 and item.get("supports") is not None:
                    results.append(item)
            except Exception as exc:
                errors.append({"ticker": ticker, "error": str(exc)})
            done += 1
            _report()
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(_analyze, t): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    _, item = future.result()
                    if item.get("score", 0) >= 45 and item.get("supports") is not None:
                        results.append(item)
                except Exception as exc:
                    errors.append({"ticker": ticker, "error": str(exc)})
                done += 1
                _report()

    results = _apply_strategy_filter(results, strategy)
    ranked, top = _rank_and_slice(results, limit, strategy)

    if strategy == "flash":
        hint = FLASH_HINT
    elif strategy == "flash_index":
        hint = FLASH_INDEX_HINT
    elif strategy == "flash_agentic":
        hint = FLASH_AGENTIC_HINT
    elif strategy == "cluster":
        hint = CLUSTER_HINT
    else:
        hint = MODE_CONFIG[mode].get("hint", "")

    payload = {
        "as_of": _utcnow(),
        "mode": mode,
        "strategy": strategy,
        "feed": feed,
        "cluster_exp": cluster_exp or "nearest",
        "concurrency": 1,
        "universe_size": len(tickers),
        "universe_meta": {
            "cutoff": UNIVERSE_META.get("cutoff"),
            "source": UNIVERSE_META.get("source"),
            "raw_count": UNIVERSE_META.get("raw_count"),
            "filtered_count": UNIVERSE_META.get("filtered_count"),
            "excluded_tiers": UNIVERSE_META.get("excluded_tiers"),
        },
        "scanned": attempted,
        "qualified": len(results),
        "actionable": sum(bool(item.get("actionable")) for item in results),
        "invalid_geometry": sum(item.get("geometry_valid") is False for item in results),
        "failed": len(errors),
        "errors": errors[:20],
        "elapsed_ms": int((time.time() - started) * 1000),
        "top": top,
        "clusters": build_cluster_groups(ranked[:40]) if strategy == "cluster" else [],
        "hint": hint,
        "caveat": (
            "Research-only reconstruction of Cipher Model Scan from public UI outputs. "
            "Not the proprietary Access Obsidian weights. Not trade advice. "
            "Scans are auto-saved locally — see scan history to revisit past runs, or download CSV. "
            "GEX is a public-OI heuristic under retail assumptions — not verified dealer positioning."
        ),
        "formula": (
            "Supports/resistances = peak |GEX| below/above spot; pull = magnet; "
            "vacuums = thin path to pull; score ≈ structure + runway + OI. "
            "Short/cluster use nearest (lowest) DTE only. "
            "Cluster ranking: hard tier quad>triple>battle then weighted factors "
            "(strength, proximity, OI, vacuum). "
            "Flash uses normalized nearest-exp floor/ceiling + session/VP/momentum proxies. "
            "Flash Agentic requires valid directional levels and reward:risk >= 1.0."
        ),
    }
    _SCAN_CACHE[cache_key] = (time.time(), payload)
    try:
        import scan_history

        scan_history.save_scan(payload)
    except Exception:
        pass  # Auto-save is best-effort; never fail a scan over it.
    return deepcopy(payload)


def start_scan_job(matrix_fn, **kwargs):
    job_id = uuid.uuid4().hex[:12]
    with _JOB_LOCK:
        _SCAN_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "done": 0,
            "total": len(kwargs.get("universe") or SCAN_UNIVERSE),
            "pct": 0,
            "message": "Queued…",
            "result": None,
            # Running leaderboard, refreshed every PARTIAL_EMIT_EVERY tickers so the
            # UI can render cards before the full scan finishes.
            "partial_top": [],
            "error": None,
            "started_at": _utcnow(),
        }

    def _worker():
        try:
            _update_job(job_id, status="running", message="Scanning the universe…")
            result = run_scan(matrix_fn, job_id=job_id, **kwargs)
            _update_job(job_id, status="done", pct=100, result=result, message="Scan complete")
        except Exception as exc:
            _update_job(job_id, status="error", error=str(exc), message=str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return job_id


def get_scan_job(job_id):
    with _JOB_LOCK:
        job = _SCAN_JOBS.get(job_id)
        if not job:
            return None
        return deepcopy(job)
