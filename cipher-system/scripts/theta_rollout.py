#!/usr/bin/env python3
"""Back up Theta ledgers and inspect/enforce the observation activation gate."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import theta_portfolio as p
from core.telegram_paper import DB as LEGACY_DB


def backup():
    parent = p.DB.parent
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='theta-backup-', dir=parent))
    for source in (LEGACY_DB, p.DB):
        if source.exists():
            src = sqlite3.connect(f'file:{source.resolve()}?mode=ro', uri=True)
            dst = sqlite3.connect(folder/source.name)
            try:
                src.backup(dst)
                if dst.execute('pragma integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup_integrity_failed')
            finally:
                src.close(); dst.close()
            (folder/source.name).chmod(0o600)
    return folder


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('action', choices=['backup', 'status', 'activate'])
    action = args.parse_args().action
    if action == 'backup':
        print(backup())
        return
    if action == 'status':
        status = p.snapshot()
        print(json.dumps({k:v for k,v in status.items() if k not in {'positions','candidates'}}, indent=2))
        return
    backup()
    db = p.connect()
    try:
        p.activate(db, datetime.now(timezone.utc))
    finally:
        db.close()


if __name__ == '__main__':
    main()
