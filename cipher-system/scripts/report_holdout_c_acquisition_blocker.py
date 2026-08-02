#!/usr/bin/env python3
"""Record the authorized-source stop condition for Holdout C recovery."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; GOV=ROOT/'data'/'governance'; QUALITY=ROOT/'data'/'market_quality'
def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'); GOV.mkdir(parents=True,exist_ok=True); QUALITY.mkdir(parents=True,exist_ok=True)
    payload={'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),'period':'2017-01-01..2019-12-31','status':'blocked_before_alternate_source_access','source_access':{'massive_polygon_sip':'missing_credentials_or_authorization','firstrate':'no_local_licensed_archive_found','alpaca_databento':'no_configured_credential_names'},'required_to_resume':['authorized Massive/Polygon SIP flat-file entitlement covering full period','or an existing licensed FirstRate 2017-2019 U.S. minute archive'], 'prohibited_actions':['purchase or subscription change without explicit user authorization','vendor mixing','ranking outcome evaluation'], 'existing_source_failure':'pinned monthly source has internal continuity gaps and no ticker-month partitions','next_action':'obtain or configure one authorized continuous primary/fallback source, then rerun representative catalog/pilot checks','live_execution':False,'promotion_eligible':False}
    p=QUALITY/f'holdout_c_alternate_source_acquisition_report_{stamp}.json';p.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');print(json.dumps({'path':str(p),'status':payload['status']},indent=2))
if __name__=='__main__':main()
