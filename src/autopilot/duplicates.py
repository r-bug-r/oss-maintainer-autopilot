"""Deterministic duplicate-candidate retrieval.

Similarity is computed locally with no model involvement. The model may later
explain a candidate, but it never decides whether one exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "make",
        "may",
        "me",
        "more",
        "most",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "other",
        "our",
        "out",
        "over",
        "same",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "up",
        "us",
        "use",
        "used",
        "using",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Candidate:
    number: int
    title: str
    score: float

    def as_dict(self) -> dict[str, object]:
        return {"number": self.number, "title": self.title, "score": round(self.score, 4)}


def tokenize(text: str) -> set[str]:
    return {
        token for token in _WORD.findall(text.lower()) if len(token) > 2 and token not in STOPWORDS
    }


def similarity(left: str, right: str) -> float:
    """Blend token overlap with title-sequence similarity.

    Both components are deterministic and bounded in [0, 1]; the blend keeps
    short titles from dominating and keeps unrelated boilerplate from matching.
    """

    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    jaccard = overlap / len(left_tokens | right_tokens)
    containment = overlap / min(len(left_tokens), len(right_tokens))
    sequence = SequenceMatcher(None, " ".join(sorted(left_tokens)), " ".join(sorted(right_tokens)))
    ratio = sequence.ratio()
    return round(0.45 * jaccard + 0.35 * containment + 0.20 * ratio, 6)


def find_candidates(
    *,
    subject_number: int,
    subject_title: str,
    subject_body: str,
    others: list[tuple[int, str, str]],
    threshold: float = 0.34,
    limit: int = 3,
) -> tuple[Candidate, ...]:
    """Rank other issues by similarity, excluding the subject itself."""

    subject_text = f"{subject_title}\n{subject_body}"
    scored: list[Candidate] = []
    for number, title, body in others:
        if number == subject_number:
            continue
        score = similarity(subject_text, f"{title}\n{body}")
        if score >= threshold:
            scored.append(Candidate(number=number, title=title, score=score))
    scored.sort(key=lambda candidate: (-candidate.score, candidate.number))
    return tuple(scored[:limit])
