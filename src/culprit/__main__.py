"""
Entry point for `python -m culprit`.

Allows running the CLI with:
    python -m culprit debug ...
"""

from culprit.cli import cli

if __name__ == "__main__":
    cli()
