from datetime import datetime, timezone
from earnings_model.forward_scorecard import score


def record(**changes):
    return dict(dict(ticker='TEST', scheduled_date='2026-08-01',
                     logged_at_utc='2026-07-31T12:00:00Z', raw_prob_day5_up=.7,
                     model_artifact_sha256='artifact-a', feature_method='method-a'), **changes)


def test_last_pre_report_forecast_counts_once():
    result = score([record(logged_at_utc='2026-07-30T12:00:00Z', raw_prob_day5_up=.2), record()],
                   {('TEST', '2026-08-01'): 3}, now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    cohort = result['cohorts'][0]
    assert cohort['mature_events'] == 1
    assert cohort['accuracy'] == 1
    assert abs(cohort['brier'] - .09) < 1e-9
    assert cohort['promotion_allowed'] is False


def test_degraded_inputs_do_not_count_toward_promotion():
    result = score([record(forecast_status='DEGRADED_INPUTS')], {('TEST', '2026-08-01'): 3}, now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert result['cohorts'] == []
    assert result['excluded']['unavailable_forecast_inputs'] == 1


def test_excludes_late_unmatured_missing_and_unidentified():
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    result = score([record(logged_at_utc='2026-08-02T12:00:00Z'),
                    record(model_artifact_sha256=None),
                    record(ticker='NEW', scheduled_date='2026-09-07'),
                    record(ticker='MISSING')], {}, now=now)
    assert result['excluded'] == dict(late_or_future_forecast=1, unidentified_cohort=1, unmatured=1, missing_outcome=1)


def test_model_and_feature_changes_are_separate_cohorts():
    result = score([record(), record(model_artifact_sha256='b'), record(feature_method='b')],
                   {('TEST', '2026-08-01'): -1}, now=datetime(2026, 9, 8, tzinfo=timezone.utc))
    assert len(result['cohorts']) == 3
    assert all(row['accuracy'] == 0 for row in result['cohorts'])


def test_invalid_probabilities_and_naive_times_are_rejected():
    result = score([record(raw_prob_day5_up=float('nan')), record(raw_prob_day5_up=2),
                    record(logged_at_utc='2026-07-31T12:00:00')], {})
    assert result['excluded']['invalid_record'] == 3


def test_frozen_training_baseline_only_scores_new_cohort():
    import hashlib
    import json
    baseline = {'probability': .6, 'training_events': 100, 'training_last_date': '2026-01-01',
                'frozen_at': '2026-07-30T00:00:00+00:00', 'source': 'unique_training_events_day5_up_v1'}
    baseline['id'] = hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest()
    records = [record(ticker='OLD'), record(forward_baseline=baseline),
               record(ticker='LATE', forward_baseline={**baseline, 'frozen_at': '2026-08-02T00:00:00+00:00'})]
    scored = score(records, {('TEST','2026-08-01'): 3, ('OLD','2026-08-01'): -2}, now=datetime(2026,9,8,tzinfo=timezone.utc))
    frozen = scored['frozen_baseline_cohorts']
    assert len(frozen) == 1 and frozen[0]['mature_events'] == 1
    assert abs(frozen[0]['baseline_brier'] - .16) < 1e-9
    assert abs(frozen[0]['brier_improvement'] - .07) < 1e-9
    assert frozen[0]['promotion_allowed'] is False
