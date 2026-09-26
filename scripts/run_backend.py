"""Start the demo backend (demo_workspace/sample_app) on http://localhost:8000.

Loads BEARER_TOKEN from the repo's .env so the server and
`culprit debug --auth-bearer` share the same demo token.
Run scripts/prepare_demo.py first.
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "demo_workspace" / "sample_app"

load_dotenv(REPO_ROOT / ".env")
if not os.environ.get("BEARER_TOKEN"):
    sys.exit("BEARER_TOKEN is not set in .env")
if not APP_DIR.exists():
    sys.exit("Run `python scripts/prepare_demo.py` first")

print("Backend: http://localhost:8000/cart/total  (Ctrl+C to stop)")
subprocess.run([sys.executable, "-m", "backend.app"], cwd=APP_DIR, check=False)
