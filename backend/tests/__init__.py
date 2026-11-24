"""Test package.

Two things every test module needs, done once here:

1. ``backend/src`` on ``sys.path`` so the suite runs with a plain
   ``python -m unittest discover -s tests -t .`` and no PYTHONPATH fiddling.
2. ``GEO_DB_PATH`` pointed at a throwaway file **before** ``geo.settings`` is
   imported. Without this the suite silently re-seeds ``backend/geo.db`` and
   wipes the demo data.
"""

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("GEO_DB_PATH", str(Path(tempfile.gettempdir()) / "geo_test_suite.db"))
