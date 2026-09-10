"""Read-only diagnostic of raw prospective probabilities; never promotes models."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import hashlib
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .config import DB_PATH
from .prospective_log import DEFAULT_LOG_PATH


def score(records, outcomes, *, now=None, _include_baselines=True):
    records = list(records)
    now = now or datetime.now(timezone.utc)
    excluded = Counter()
    selected = {}
    for row in records:
        if row.get('forecast_status') in {'UNAVAILABLE', 'DEGRADED_INPUTS'}:
            excluded['unavailable_forecast_inputs'] += 1
            continue
        if row.get('raw_prob_day5_up') is None:
            excluded['missing_raw_probability'] += 1
            continue
        try:
            probability = float(row['raw_prob_day5_up'])
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError('probability')
            stamp = datetime.fromisoformat(row['logged_at_utc'].replace('Z', '+00:00'))
            report = datetime.fromisoformat(row['scheduled_date'][:10]).replace(tzinfo=ZoneInfo('America/New_York'))
            if stamp.tzinfo is None:
                raise ValueError('timezone missing')
            cohort = (row.get('model_artifact_sha256'), row.get('feature_method'))
            if not all(cohort):
                excluded['unidentified_cohort'] += 1
                continue
            # Timing is often unconfirmed: require forecast before report-day midnight.
            if stamp >= report or stamp > now:
                excluded['late_or_future_forecast'] += 1
                continue
            key = (*cohort, row['ticker'].upper(), report.date().isoformat())
        except (ValueError, TypeError, KeyError):
            excluded['invalid_record'] += 1
            continue
        if key in selected:
            excluded['duplicate_event_forecast'] += 1
        if key not in selected or stamp > selected[key][0]:
            selected[key] = (stamp, report, probability)
    groups = defaultdict(list)
    for key, (_, report, probability) in selected.items():
        groups[key[:2]]  # Keep waiting cohorts visible.
        if now < report + timedelta(days=11):
            excluded['unmatured'] += 1
            continue
        outcome = outcomes.get(key[2:])
        if outcome is None or not math.isfinite(outcome):
            excluded['missing_outcome'] += 1
            continue
        groups[key[:2]].append((probability, int(outcome > 0)))
    cohorts = []
    for (digest, method), rows in sorted(groups.items()):
        gated = [(p, y) for p, y in rows if p >= 0.58 or p <= 0.42]
        cohorts.append({
            'model_artifact_sha256': digest, 'feature_method': method,
            'mature_events': len(rows), 'qualified_events': len(gated),
            'accuracy': sum((p >= .5) == y for p, y in rows) / len(rows) if rows else None,
            'qualified_accuracy': sum((p >= .5) == y for p, y in gated) / len(gated) if gated else None,
            'brier': sum((p-y)**2 for p, y in rows) / len(rows) if rows else None,
            'promotion_allowed': False,
            'blockers': ['Frozen baseline comparison not yet available; diagnostics do not authorize promotion.'],
        })
    result = {'cohorts': cohorts, 'excluded': dict(excluded), 'promotion_allowed': False}
    if _include_baselines:
        result['frozen_baseline_cohorts'] = frozen_comparison(records, outcomes, now=now)
    return result


def frozen_comparison(records, outcomes, *, now):
    """Separate forward cohorts; never retrofit baselines onto old forecasts."""
    groups = defaultdict(list)
    for row in records:
        baseline = row.get('forward_baseline')
        if not isinstance(baseline, dict):
            continue
        try:
            identity = baseline['id']
            body = {k: v for k, v in baseline.items() if k != 'id'}
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != identity:
                continue
            p = float(baseline['probability'])
            frozen = datetime.fromisoformat(baseline['frozen_at'].replace('Z', '+00:00'))
            logged = datetime.fromisoformat(row['logged_at_utc'].replace('Z', '+00:00'))
            if not math.isfinite(p) or not 0 <= p <= 1 or not frozen.tzinfo or not logged.tzinfo or frozen > logged or baseline['training_events'] < 1 or baseline['training_last_date'] >= baseline['frozen_at'][:10]:
                continue
            groups[(row.get('model_artifact_sha256'), row.get('feature_method'), identity)].append(row)
        except (ValueError, TypeError, KeyError):
            continue
    result = []
    for (digest, method, identity), group in groups.items():
        actual = score(group, outcomes, now=now, _include_baselines=False)['cohorts']
        # The baseline must be scored over the exact same eligible events as
        # the model (including excluding invalid model probabilities).
        valid = []
        for row in group:
            try:
                p = float(row['raw_prob_day5_up'])
                if math.isfinite(p) and 0 <= p <= 1:
                    valid.append({**row, 'raw_prob_day5_up': row['forward_baseline']['probability']})
            except (KeyError, ValueError, TypeError):
                pass
        base = score(valid, outcomes, now=now, _include_baselines=False)['cohorts']
        if actual and base:
            a, b = actual[0], base[0]
            result.append({**a, 'baseline_id': identity, 'baseline_probability': group[0]['forward_baseline']['probability'],
                           'baseline_accuracy': b['accuracy'], 'baseline_brier': b['brier'],
                           'brier_improvement': b['brier']-a['brier'] if a['brier'] is not None else None,
                           'blockers': ['Forward evidence only; existing qualification and promotion gates still apply.']})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument('--db', type=Path, default=Path(DB_PATH))
    args = parser.parse_args()
    records, malformed = [], 0
    for line in args.log.read_text().splitlines():
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('not an object')
            records.append(row)
        except ValueError:
            malformed += 1
    with sqlite3.connect(f'file:{args.db.resolve()}?mode=ro', uri=True) as db:
        outcomes = {(str(symbol).upper(), str(day)[:10]): value for symbol, day, value in db.execute(
            'select symbol, earnings_date, day5_return_pct from price_impact'
        )}
    result = score(records, outcomes)
    result['malformed_lines'] = malformed
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
