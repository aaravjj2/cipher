#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/home/aarav/Aarav/cipher/runtime/data/gex_history.sqlite')
cursor = conn.cursor()

# Get latest captured_at from gex_snapshots
cursor.execute('SELECT MAX(captured_at) FROM gex_snapshots')
max_captured = cursor.fetchone()[0]
print(f"Latest gex_snapshots captured_at: {max_captured}")

# Also check gex_capture_runs
cursor.execute('SELECT MAX(completed_at) FROM gex_capture_runs')
max_completed = cursor.fetchone()[0]
print(f"Latest gex_capture_runs completed_at: {max_completed}")

# Count recent snapshots
cursor.execute("SELECT COUNT(*) FROM gex_snapshots WHERE captured_at > datetime('now', '-1 hour')")
recent = cursor.fetchone()[0]
print(f"Snapshots in last hour: {recent}")

cursor.execute("SELECT COUNT(*) FROM gex_snapshots WHERE captured_at > datetime('now', '-24 hour')")
recent24 = cursor.fetchone()[0]
print(f"Snapshots in last 24 hours: {recent24}")

conn.close()