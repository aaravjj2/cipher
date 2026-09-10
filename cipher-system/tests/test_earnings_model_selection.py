from __future__ import annotations

import numpy as np
import pandas as pd

from earnings_model.model import FEATURE_COLS, MODEL_VERSION, train_earnings_models
from earnings_model import model, discord_bot


def test_inference_does_not_train_when_artifact_is_missing(monkeypatch):
    monkeypatch.setattr(model, 'load_trained_models', lambda: None)
    monkeypatch.setattr(model, 'train_earnings_models', lambda: (_ for _ in ()).throw(AssertionError('must not train')))
    assert 'artifact unavailable' in model.predict_for_symbol('TEST', conn=object())['error']


def test_unvalidated_raw_probability_is_visible_without_authorizing_entry(monkeypatch):
    class Estimator:
        def predict_proba(self, _frame):
            return np.array([[0.27, 0.73]])
        def predict(self, _frame):
            return np.array([2.0])
    monkeypatch.setattr(model, 'load_trained_models', lambda: {
        'artifacts': {key: Estimator() for key in ['day1_direction', 'day5_direction', 'gap_reversal', 'expected_abs_gap']},
        'feature_cols': FEATURE_COLS, 'results': {'strategy_gate': {'paper_entry_allowed': False}},
    })
    monkeypatch.setattr(model, 'build_feature_dataset', lambda *a, **kw: pd.DataFrame([{c: 0.0 for c in FEATURE_COLS}]))
    monkeypatch.setattr(model, 'upcoming_event_features', lambda frame: pd.Series({**frame.iloc[-1].to_dict(), 'earnings_date': '2026-01-01'}))
    prediction = model.predict_for_symbol('TEST', conn=object())
    assert prediction['prob_day5_up'] == 0.73
    assert prediction['raw_confidence'] == 0.73
    assert prediction['forecast_status'] == 'UNVALIDATED'
    assert prediction['strategy_eligible'] is False
    assert prediction['primary_strategy'].startswith('NO TRADE')
    payload = discord_bot.build_this_week_preview_embed([{
        'symbol': 'TEST', 'scheduled_date': '2026-09-10', 'prob_day5_up': 0.73,
        'forecast_status': prediction['forecast_status'], 'recommended_strategy': prediction['primary_strategy'],
    }])
    value = payload['embeds'][0]['fields'][0]['value']
    assert '73.0%' in value and 'UNVALIDATED' in value and 'NO TRADE' in value
    assert '**Confidence**' not in value


def test_missing_outcomes_are_not_negative_training_labels(monkeypatch):
    row = {c: 0.0 for c in FEATURE_COLS}
    row.update(symbol='TEST', earnings_date='2026-09-01', gap_pct=1.0,
               day1_return_pct=None, day5_return_pct=None, eps_actual=None,
               eps_estimate=1.0, eps_surprise_pct=None)
    monkeypatch.setattr(model.pd, 'read_sql_query', lambda *a, **kw: pd.DataFrame([row]))
    data = model.build_feature_dataset(conn=object())
    assert data['target_day1_up'].isna().all()
    assert data['target_day5_up'].isna().all()
    assert data['is_beat_num'].isna().all()


def _dataset(rows: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    frame = pd.DataFrame({name: rng.normal(size=rows) for name in FEATURE_COLS})
    frame["earnings_date"] = pd.date_range("2020-01-01", periods=rows, freq="D").astype(str)
    frame["target_day1_up"] = rng.integers(0, 2, rows)
    frame["target_day5_up"] = rng.integers(0, 2, rows)
    frame["target_reversal"] = rng.integers(0, 2, rows)
    frame["target_abs_gap"] = np.maximum(0.1, 2.0 + frame[FEATURE_COLS[0]] * 0.8)
    return frame


def test_model_selection_uses_three_chronological_windows_and_baselines():
    result = train_earnings_models(_dataset(), save_artifacts=False)
    assert result["model_version"] == MODEL_VERSION
    assert (result["train_samples"], result["validation_samples"], result["test_samples"]) == (350, 110, 120)
    assert result['embargo_days'] == 10
    assert result['evaluated_estimator'] == 'development_refit_exact_saved_model'
    direction = result["models"]["day5_direction"]
    assert direction["selected_candidate"] in direction["validation_candidates"]
    assert direction["baseline_accuracy"] is not None
    assert direction["baseline_brier"] is not None
    assert direction["trade_eligible"] is False
    assert result["strategy_gate"]["paper_entry_allowed"] is False


def test_expected_gap_requires_holdout_improvement_over_naive_median():
    result = train_earnings_models(_dataset(), save_artifacts=False)
    gap = result["models"]["expected_abs_gap"]
    assert gap["mae_pct"] < gap["baseline_mae_pct"]
    assert gap["mae_improvement_pct"] >= 5.0
    assert gap["trade_eligible"] is True


def test_returned_estimators_match_the_reported_holdout_metrics():
    from sklearn.metrics import brier_score_loss, mean_absolute_error
    frame = _dataset()
    train, selection, holdout = frame.iloc[:350], frame.iloc[360:470], frame.iloc[480:]
    estimator, metrics = model._fit_classifier(train, selection, holdout, 'target_day5_up')
    probability = estimator.predict_proba(holdout[FEATURE_COLS])[:, 1]
    assert metrics['brier'] == round(brier_score_loss(holdout['target_day5_up'], probability), 4)
    estimator, metrics = model._fit_gap_regressor(train, selection, holdout)
    prediction = np.maximum(0, estimator.predict(holdout[FEATURE_COLS]))
    assert metrics['mae_pct'] == round(mean_absolute_error(holdout['target_abs_gap'], prediction), 4)


def test_duplicate_report_dates_never_cross_embargo_boundaries():
    frame = _dataset()
    frame['earnings_date'] = np.repeat(pd.date_range('2020-01-01', periods=200).astype(str), 3)
    result = train_earnings_models(frame, save_artifacts=False)
    splits = result['split_dates']
    assert (pd.Timestamp(splits['validation_first']) - pd.Timestamp(splits['train_last'])).days > 10
    assert (pd.Timestamp(splits['test_first']) - pd.Timestamp(splits['validation_last'])).days > 10


def test_upcoming_features_advance_priors_but_exclude_unmatured_and_future_reports():
    rows = []
    for date, beat, surprise in [('2026-01-01', 0, -10), ('2026-04-01', 1, 20),
                                  ('2026-07-01', 1, 30), ('2026-09-05', 0, -999),
                                  ('2026-12-01', 0, -999)]:
        rows.append(dict(earnings_date=date, is_beat_num=beat, eps_surprise_pct=surprise,
                         target_abs_gap=2, day5_return_pct=3, reversal_num=0,
                         prior_beat_rate=0.5, prior_streak=1))
    result = model.upcoming_event_features(pd.DataFrame(rows), as_of='2026-09-08')
    assert result['earnings_date'] == '2026-07-01'
    assert result['prior_beat_rate'] == 2 / 3
    assert result['prior_streak'] == 2
    assert result['prior_avg_surprise'] == 40 / 3


def test_upcoming_priors_do_not_turn_unknown_earnings_into_misses():
    frame = pd.DataFrame([
        dict(earnings_date='2026-01-01', is_beat_num=1, eps_surprise_pct=10,
             target_abs_gap=2, day5_return_pct=3, reversal_num=0),
        dict(earnings_date='2026-04-01', is_beat_num=np.nan, eps_surprise_pct=np.nan,
             target_abs_gap=np.nan, day5_return_pct=np.nan, reversal_num=np.nan),
    ])
    result = model.upcoming_event_features(frame, as_of='2026-09-08')
    assert result['prior_beat_rate'] == 1
    assert result['prior_avg_surprise'] == 10
    assert result['prior_streak'] == 0
