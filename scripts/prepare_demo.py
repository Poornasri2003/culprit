"""Create a fresh, broken copy of sample_app for a Culprit demo run.

Copies sample_app/ to demo_workspace/sample_app/ (git-ignored) and makes it
its own git repository with one baseline commit, so the commit Culprit makes
when it fixes the bug lands there, never in the Culprit repo itself.
Run it again any time to reset the demo.
"""

import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "sample_app"
TARGET = REPO_ROOT / "demo_workspace" / "sample_app"


def _force_remove(func, path, _exc):
    Path(path).chmod(stat.S_IWRITE)  # git objects are read-only on Windows
    func(path)


def main() -> None:
    # Empty the folder rather than deleting it: on Windows a terminal whose
    # current directory is TARGET would otherwise make the reset fail.
    if TARGET.exists():
        for child in TARGET.iterdir():
            if child.is_dir():
                shutil.rmtree(child, onexc=_force_remove)
            else:
                _force_remove(Path.unlink, child, None)
    shutil.copytree(
        SOURCE, TARGET,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "test_culprit_*.py"),
        dirs_exist_ok=True,
    )

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=TARGET, check=True, capture_output=True)

    (TARGET / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    git("init", "-q")
    git("config", "user.name", "Culprit Demo")
    git("config", "user.email", "demo@culprit.local")
    git("add", "-A")
    git("commit", "-q", "-m", "Baseline: sample app with seeded pricing bug")
    print(f"Demo workspace ready: {TARGET}")


if __name__ == "__main__":
    main()
