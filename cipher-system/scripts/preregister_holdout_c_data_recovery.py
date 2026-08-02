#!/usr/bin/env python3
"""Freeze governance and source policy before alternate-source access."""
from __future__ import annotations
import hashlib, json, os, platform, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; GOV=ROOT/'data'/'governance'; QUALITY=ROOT/'data'/'market_quality'
def digest(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); GOV.mkdir(parents=True,exist_ok=True)
    files=[GOV/'research_status_registry.json', QUALITY/'holdout_c_contamination_availability_20260802T195011Z.json', ROOT/'core'/'research_platform'/'market_quality.py', ROOT/'docs'/'price_only_forecast_gate.md', ROOT/'docs'/'foundation_model_branch_closeout.md']
    env_names=sorted(k for k in os.environ if any(x in k.upper() for x in ('MASSIVE','POLYGON','FIRST','DATABENTO','ALPACA')))
    prereg={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'period':['2017-01-01','2019-12-31'],'minimum_common_tickers':8,'minimum_strict_independent_20_session_origins':12,'minimum_ticker_origin_observations':100,'preferred_ticker_origin_observations':150,'no_outcome_evaluation_before_panel_freeze':True,'source_priority':['Massive/Polygon us_stocks_sip/minute_aggs_v1','FirstRate licensed archive','Alpaca/Databento metadata diagnostics only'],'vendor_mixing_prohibited':True,'pilot_acceptance':['reproducible access','compatible timestamp semantics','no systematic OHLC corruption','better continuity','path to 8 tickers and 12 origins'],'stop_conditions':['no authorized primary/fallback access','pilot failure','storage safety margin failure'],'principal_endpoint':'momentum_20 predicting 20-session cross-sectional return, origin-level Spearman IC','signals':['momentum_5','momentum_20','momentum_60','reversal_5','reversal_20','vol_scaled_momentum_20','trend_consistency_20','ma_distance_20','classical_trend_ensemble'],'bootstrap_seed':42,'verdicts':['reject','inconclusive','validated_research_signal'],'safety':'price_forecast_research_only_no_volume_features; no execution or promotion'}
    manifest={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'workspace':{'git_worktree':False,'reason':'no .git directory discovered','python':platform.python_version(),'storage':shutil.disk_usage(ROOT).free,'data_directory_bytes':sum(p.stat().st_size for p in (ROOT/'data').rglob('*') if p.is_file()),'credential_variable_names':env_names},'evidence':[{'path':str(p),'sha256':digest(p)} for p in files]}
    p=GOV/f'holdout_c_data_recovery_preregistration_{stamp}.json'; m=GOV/f'holdout_c_data_recovery_manifest_{stamp}.json'; p.write_text(json.dumps(prereg,indent=2,sort_keys=True)+'\n');m.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n');print(json.dumps({'preregistration':str(p),'manifest':str(m),'credential_variable_names':env_names},indent=2))
if __name__=='__main__': main()
