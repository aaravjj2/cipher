from core import backtest_protocol as protocol


def _spec(**changes):
    values = dict(
        mode="standalone", symbols=["NVDA", "AAPL"], timeframe="15Min",
        years=1, detector_mode="EOD Focus", lookback_bars=6, entry_every=12,
        control_repeats=20, stop_atr=1, target_atr=1.5, max_hold_bars=24,
        slippage_bps_per_side=2, commission_bps_per_side=0,
        holdout_fraction=.3, embargo_bars=1, seed=17,
    )
    values.update(changes)
    return protocol.experiment_spec(**values)


def test_parameter_lock_is_stable_and_sensitive():
    assert protocol.parameter_lock_hash(_spec()) == protocol.parameter_lock_hash(_spec())
    assert protocol.parameter_lock_hash(_spec(stop_atr=1.1)) != protocol.parameter_lock_hash(_spec())


def test_product_protocol_requires_explicit_costs():
    try:
        _spec(slippage_bps_per_side=None)
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("missing costs were accepted")


def test_split_is_chronological_purged_and_reports_ineligible_symbols():
    bars = {
        "NVDA": [{"time": f"2026-01-{i:03d}", "open": i} for i in range(400)],
        "TINY": [{"time": f"2026-01-{i:03d}", "open": i} for i in range(20)],
    }
    train, holdout, coverage = protocol.split_bars(
        bars, holdout_fraction=.3, embargo_bars=2, minimum_bars=100,
    )
    assert train["NVDA"][-1]["time"] < holdout["NVDA"][0]["time"]
    assert coverage["NVDA"]["embargo_bars"] == 4
    assert "TINY" not in train and coverage["TINY"]["eligible"] is False


def test_bootstrap_is_deterministic_and_labels_small_samples():
    returns = [-1, .5, .75, 1.2, -.4, .3, .9, -.2, 1.1, .4, .2, .6]
    assert protocol.bootstrap_mean_interval(returns, seed=9) == protocol.bootstrap_mean_interval(returns, seed=9)
    assert protocol.bootstrap_mean_interval([1, 2], seed=9)["interval"] is None


def test_moving_block_bootstrap_is_deterministic_and_declares_serial_method():
    returns = [-1, -.8, -.6, .5, .7, .9, -1.1, -.7, .4, .8, 1.0, .6]
    first = protocol.moving_block_bootstrap_mean_interval(
        returns, seed=9, block_length=3, repeats=200,
    )
    second = protocol.moving_block_bootstrap_mean_interval(
        returns, seed=9, block_length=3, repeats=200,
    )
    assert first == second
    assert first["method"] == "circular_moving_block_bootstrap_mean_95pct"
    assert first["block_length"] == 3 and first["n"] == len(returns)


def test_moving_block_bootstrap_rejects_invalid_block_length():
    try:
        protocol.moving_block_bootstrap_mean_interval(range(12), seed=1, block_length=13)
    except ValueError as exc:
        assert "cannot exceed" in str(exc)
    else:
        raise AssertionError("oversized serial bootstrap block was accepted")


def test_manifest_never_grants_live_authority():
    coverage = {
        "NVDA": {"eligible": True, "all": {"sha256": "a"}},
        "TINY": {"eligible": False, "all": {"sha256": "b"}},
    }
    manifest = protocol.build_manifest(_spec(), coverage)
    assert manifest["validation_status"] == "eligible"
    assert manifest["live_order_authority"] is False
    assert manifest["blocked_symbols"] == ["TINY"]
    assert len(manifest["run_id"]) == 64 and len(manifest["data_fingerprint"]) == 64
    assert manifest["spec"]["validation"]["uncertainty"]["block_length_trades"] == 5


def test_portfolio_capacity_and_equity_are_deterministic():
    class T:
        def __init__(self, symbol, entry, exit_, ret):
            self.symbol, self.entry_time, self.exit_time = symbol, entry, exit_
            self.return_pct = ret
    trades = [T("A", "01", "05", 10), T("B", "02", "06", 10), T("C", "03", "04", 99)]
    result = protocol.portfolio_summary(trades, max_concurrent_positions=2, position_fraction=.1)
    assert result["trades_taken"] == 2
    assert result["trades_skipped_at_capacity"] == 1
    assert result["ending_equity"] == 102010.0
