"""Grounded Q&A over an existing pack. Used by `compass ask` and `/ask`."""
from __future__ import annotations

from pathlib import Path

from compass.domain.models import AskAnswer


def answer_question(*, pack_dir: Path, question: str, top_k: int = 6) -> AskAnswer:
    """Retrieve top-k chunks from pack_dir/notes/, ground an LLM answer, return AskAnswer."""
    notes_dir = pack_dir / "notes"
    if not notes_dir.exists():
        raise FileNotFoundError(f"missing notes/ under {pack_dir}")
    # TODO(compass): BM25 over the notes, then call Bob (or watsonx.ai) with a
    # strict "answer only from provided context; refuse if not found" prompt.
    raise NotImplementedError("ask.answer_question")
