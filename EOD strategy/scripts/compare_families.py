#!/usr/bin/env python3
"""Compare families and symbols across a sweep: where, if anywhere, an edge lives.

A single crowned candidate answers "did this configuration win its own selection", which is
the question a sweep is worst at answering honestly -- the winner is chosen partly by noise.
This report asks the two questions that survive that problem:

* **Across families.** Does the edge prefer one-minute or five-minute bars, the closing thirty
  minutes or the whole session? A signal that appears in one family and vanishes in the others
  is a property of that window, not of the market.
* **Across symbols.** Which names carry it, and does any name carry it in more than one family?
  A ticker positive under both bar sizes and both windows is a far stronger claim than a ticker
  positive once, because the four families are different enough that agreement is not automatic.

Everything is reported equal-weight compounded and annualized against the risk-free rate,
because a pooled sum of per-trade percentages across dozens of symbols is not a return and
reads roughly N times larger than one.

Selection intensity is reported alongside every result: with hundreds of candidates against a
fixed holdout, some family producing an attractive number is expected. The count of candidates
that had to be searched to find it is part of the result, not a footnote.

Research-only. Not trading advice.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "core")):
    if path not in sys.path:
        sys.path.insert(0, path)

from core.holdout_economics import (  # noqa: E402
    RISK_FREE_PCT,
    _compound_pct,
    _holdout_economics,
)

RESULTS = ROOT / "results"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(out)


def _family_trades(family: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for row in family.get("holdout_rows") or [] for t in row.get("trades") or []]


def _selection_intensity(family: dict[str, Any]) -> dict[str, Any]:
    """How hard the sweep had to look. Context for any number it found.

    The saved run stores these as counts (`grid_size`,
    `selection_eligible_candidates`, `strict_all_folds_positive_candidates`) rather than the
    per-candidate array, which lives only in the checkpoint. Reading the array here produced a
    silent `0 / 0` in the rendered table — a report claiming nothing was searched.
    """
    return {
        "candidates_evaluated": int(family.get("grid_size") or 0),
        "passed_activity_and_breadth": int(family.get("selection_eligible_candidates") or 0),
        "positive_in_all_folds": int(family.get("strict_all_folds_positive_candidates") or 0),
        "crowned": bool(family.get("chosen_from_training")),
    }


def analyse(run: dict[str, Any]) -> dict[str, Any]:
    data = run.get("data") or {}
    holdout = data.get("holdout") or {}
    start = date.fromisoformat(holdout["start"])
    end = date.fromisoformat(holdout["end"])
    universe = list(data.get("symbols") or [])
    risk_free_pct = float(data.get("risk_free_pct", RISK_FREE_PCT))

    families: list[dict[str, Any]] = []
    # symbol -> family -> compounded %, for the cross-family agreement view.
    per_symbol: dict[str, dict[str, float]] = defaultdict(dict)
    # Track whether a symbol was positive in each chronological development
    # fold of each crowned family. Holdout agreement alone can still be one
    # regime; development consistency adds an independent robustness dimension.
    per_symbol_training: dict[str, list[bool]] = defaultdict(list)

    for family in run.get("families") or []:
        name = family.get("family") or "?"
        intensity = _selection_intensity(family)
        trades = _family_trades(family)
        entry: dict[str, Any] = {
            "family": name,
            "params": (family.get("chosen_from_training") or {}).get("params"),
            "selection": intensity,
            "economics": None,
        }
        if trades:
            econ = _holdout_economics(
                trades, start, end, universe=universe, risk_free_pct=risk_free_pct
            )
            entry["economics"] = econ
            for symbol, value in econ["per_symbol_compounded_pct"].items():
                per_symbol[symbol][name] = value
            chosen = family.get("chosen_from_training") or {}
            for fold in chosen.get("folds") or []:
                stats = fold.get("symbol_stats") or {}
                for symbol in universe:
                    avg = (stats.get(symbol) or {}).get("avg_trade_return_pct")
                    per_symbol_training[symbol].append(bool(avg is not None and avg > 0))
        families.append(entry)

    # A symbol's agreement across families. Only families that crowned a candidate count, since
    # a family with no candidate is silent rather than negative.
    scored_families = [f["family"] for f in families if f["economics"]]
    agreement = []
    for symbol, by_family in sorted(per_symbol.items()):
        values = [by_family.get(f) for f in scored_families]
        present = [v for v in values if v is not None]
        positive = [v for v in present if v > 0]
        training = per_symbol_training.get(symbol, [])
        agreement.append({
            "symbol": symbol,
            "per_family_pct": {f: by_family.get(f) for f in scored_families},
            "families_scored": len(present),
            "families_positive": len(positive),
            "mean_pct": sum(present) / len(present) if present else None,
            "worst_pct": min(present) if present else None,
            "development_folds_positive": sum(training),
            "development_folds_scored": len(training),
            "positive_in_all": len(present) > 1 and len(positive) == len(present),
            "positive_in_all_development_folds": bool(training) and all(training),
            "consistent_train_and_holdout": (
                len(present) > 1 and len(positive) == len(present) and bool(training) and all(training)
            ),
        })
    agreement.sort(key=lambda row: (
        -int(row["consistent_train_and_holdout"]),
        -row["families_positive"],
        -row["development_folds_positive"],
        -(row["mean_pct"] or -1e9),
    ))

    return {
        "holdout": {"start": holdout.get("start"), "end": holdout.get("end")},
        "universe_size": len(universe),
        "families": families,
        "symbol_agreement": agreement,
        "risk_free_pct": risk_free_pct,
    }


def render(analysis: dict[str, Any], source: Path) -> str:
    parts: list[str] = []
    parts.append("# Obsidian EOD — where the edge lives")
    parts.append("")
    parts.append(
        f"From `{source.name}`. Holdout {analysis['holdout']['start']} → "
        f"{analysis['holdout']['end']}, universe {analysis['universe_size']} symbols. "
        "Research-only reconstruction; not trading advice."
    )
    parts.append("")

    parts.append("## Families")
    parts.append("")
    parts.append(
        "`Annualized %` is equal-weight compounded per symbol then annualized — not the pooled "
        "sum, which adds per-trade percentages across every symbol and is not a return. "
        "`Searched` is how many candidates were evaluated to produce the crowned one; a large "
        "number next to a small excess is the shape of an overfit."
    )
    parts.append("")
    rows = []
    for family in analysis["families"]:
        econ = family["economics"]
        sel = family["selection"]
        if not econ:
            rows.append([
                family["family"], "—", "—", "—", "—",
                f"{sel['candidates_evaluated']} / {sel['positive_in_all_folds']}",
                "no candidate crowned",
            ])
            continue
        worst = econ["leave_one_out"][0] if econ["leave_one_out"] else None
        rows.append([
            family["family"],
            f"{econ['equal_weight_pct']:+.3f}",
            f"{econ['annualized_pct']:+.2f}",
            f"{econ['excess_vs_risk_free_pp']:+.2f}",
            f"{econ['positive_symbols']}/{econ['symbols']}",
            f"{sel['candidates_evaluated']} / {sel['positive_in_all_folds']}",
            (f"{worst['symbol']} → {worst['annualized_pct']:+.2f}%" if worst else "—"),
        ])
    parts.append(_md_table(
        ["Family", "Equal-weight %", "Annualized %", "vs risk-free (pp)",
         "Positive symbols", "Searched / all-folds+", "Worst leave-one-out"],
        rows,
    ))
    parts.append("")

    parts.append("## Symbols across families")
    parts.append("")
    parts.append(
        "Compounded holdout return per symbol, one column per family that crowned a candidate. "
        "`All holdout +` means positive in every crowned family; `Train+holdout` is stricter and "
        "also requires the symbol to be positive in every chronological development fold of "
        "those families. The tested families differ in bar size and session window, so agreement "
        "is more useful than a one-family winner."
    )
    parts.append("")
    scored = [f["family"] for f in analysis["families"] if f["economics"]]
    headers = ["Symbol"] + scored + [
        "Mean %", "Worst %", "Pos/Scored", "Dev + folds", "All holdout +", "Train+holdout"
    ]
    rows = []
    for row in analysis["symbol_agreement"]:
        cells = [row["symbol"]]
        for family in scored:
            value = row["per_family_pct"].get(family)
            cells.append("—" if value is None else f"{value:+.2f}")
        cells.append("—" if row["mean_pct"] is None else f"{row['mean_pct']:+.2f}")
        cells.append("—" if row["worst_pct"] is None else f"{row['worst_pct']:+.2f}")
        cells.append(f"{row['families_positive']}/{row['families_scored']}")
        cells.append(f"{row['development_folds_positive']}/{row['development_folds_scored']}")
        cells.append("yes" if row["positive_in_all"] else "")
        cells.append("yes" if row["consistent_train_and_holdout"] else "")
        rows.append(cells)
    parts.append(_md_table(headers, rows))
    parts.append("")

    survivors = [
        r["symbol"] for r in analysis["symbol_agreement"] if r["consistent_train_and_holdout"]
    ]
    parts.append("## Reading")
    parts.append("")
    if not scored:
        parts.append(
            "No family crowned a candidate, so there is nothing to compare. Under the strict "
            "all-folds-positive rule that is a result, not a failure: no configuration was "
            "positive across every development fold."
        )
    elif len(scored) < 2:
        # Stated explicitly rather than reported as "no symbol was positive in every family",
        # which is true but structural: cross-family agreement needs at least two families, so
        # that phrasing would present an unavailable test as a negative finding.
        parts.append(
            f"Only one family (`{scored[0]}`) crowned a candidate, so the cross-family agreement "
            "test does not apply — it needs at least two. The per-symbol column below is a single "
            "family's holdout return, which cannot distinguish a symbol that carries a signal "
            "from one that happened to rise during the window."
        )
    elif survivors:
        parts.append(
            f"**{len(survivors)} of {len(analysis['symbol_agreement'])} symbols stayed positive "
            f"through every scored development fold and every crowned-family holdout**: "
            f"{', '.join(survivors)}. That is the strongest shortlist worth further work. "
            "It is not a validated edge — the holdout is one short window, the families share "
            "the same underlying bars, and agreement across windows on one historical path is "
            "weaker evidence than it looks."
        )
    else:
        parts.append(
            "**No symbol survived the strict train-and-holdout consistency test across every "
            "crowned family.** That is informative: any apparent ticker edge depends on at least "
            "one development regime or one session/bar-size choice rather than remaining stable "
            "through all of them."
        )
    parts.append("")
    beats = [f for f in analysis["families"] if f["economics"] and f["economics"]["beats_risk_free"]]
    if beats:
        parts.append(
            "Families clearing the risk-free rate: "
            + ", ".join(
                f"`{f['family']}` ({f['economics']['annualized_pct']:+.2f}%)" for f in beats
            )
            + ". Check each against its leave-one-out column before treating it as real — a "
            "result that disappears without one symbol is that symbol's."
        )
    else:
        parts.append(
            f"**No family cleared the {analysis['risk_free_pct']:.0f}% risk-free rate.** Widening "
            "the search across bar sizes, session windows, indicator lengths and the full tested "
            "universe did not produce a configuration that beats holding cash over this holdout."
        )
    parts.append("")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--optimization",
        type=Path,
        default=RESULTS / "obsidian_eod_optimization_2026_universe41.json",
    )
    parser.add_argument("--slug", default="obsidian_eod_family_comparison")
    args = parser.parse_args(argv)

    source = args.optimization.resolve()
    if not source.exists():
        raise SystemExit(f"optimization run not found: {source}")
    run = json.loads(source.read_text(encoding="utf-8"))

    analysis = analyse(run)
    md = RESULTS / f"{args.slug}.md"
    js = RESULTS / f"{args.slug}.json"
    md.write_text(render(analysis, source), encoding="utf-8")
    js.write_text(json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {md}")
    print(f"wrote {js}")
    for family in analysis["families"]:
        econ = family["economics"]
        if econ:
            print(
                f"  {family['family']:26s} annualized={econ['annualized_pct']:+7.2f}%  "
                f"beats_rf={econ['beats_risk_free']}  positive={econ['positive_symbols']}/{econ['symbols']}"
            )
        else:
            print(f"  {family['family']:26s} no candidate crowned")
    survivors = [
        r["symbol"] for r in analysis["symbol_agreement"] if r["consistent_train_and_holdout"]
    ]
    print(f"  strict train+holdout symbol survivors: {len(survivors)} {survivors or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
