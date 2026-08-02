from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Legacy tests use paths relative to the active cipher-system checkout. Keep the
# test process anchored there regardless of where pytest was invoked.
os.chdir(ROOT)
