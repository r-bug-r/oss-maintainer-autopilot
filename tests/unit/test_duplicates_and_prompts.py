from __future__ import annotations

from autopilot.duplicates import find_candidates, similarity, tokenize
from autopilot.prompts import PROMPT_VERSION, build_prompt, injection_signals


def test_tokenize_drops_stopwords_and_punctuation() -> None:
    tokens = tokenize("The config file is empty, and it crashes!")
    assert "the" not in tokens
    assert "and" not in tokens
    assert {"config", "file", "empty", "crashes"} <= tokens


def test_identical_text_scores_one() -> None:
    assert similarity("crash on empty config", "crash on empty config") == 1.0


def test_unrelated_text_scores_low() -> None:
    assert similarity("crash on empty config", "please add dark mode") < 0.34


