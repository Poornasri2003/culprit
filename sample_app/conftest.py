"""pytest configuration — makes sample_app/ a Python path root.

This allows `from backend.app import app` and `from shared.pricing import …`
to resolve correctly regardless of the directory pytest is invoked from.
"""

import sys
from pathlib import Path

# Add sample_app/ to sys.path so that `backend`, `frontend`, and `shared`
# are importable as top-level packages.
sys.path.insert(0, str(Path(__file__).parent))
