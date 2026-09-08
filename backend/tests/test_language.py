"""Copy-honesty lint.

CLAUDE.md rule 2: confidence, never certainty. Every user-facing string —
detector output, report prose, API messages, and the frontend — says what was
observed and how confident the system is, never what someone did.

This runs as a test because a language rule that lives only in a style guide is
a language rule that gets broken at 3am on demo night.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from backend.config import REPO_ROOT

# Words that assert guilt or certainty about a person. Their presence in a
# user-facing string is the failure — an accusation is not ours to make.
ACCUSATORY = [
    "criminal", "scammer", "fraudster", "thief", "stole", "stolen by",
    "guilty", "perpetrator", "culprit", "offender", "launderer",
]

# Words that overclaim what a heuristic can know.
OVERCLAIM = [
    "proves", "proven", "definitely", "certainly", "guaranteed",
    "confirms that", "without doubt", "conclusive",
]

# Claims about how the weights were produced. They are hand-tuned constants;
# saying otherwise is the specific overclaim CLAUDE.md names.
ML_CLAIMS = ["machine learning", "neural", "trained model", "our model predicts"]

SOURCE_DIRS = [
    REPO_ROOT / "backend",
    REPO_ROOT / "frontend" / "src",
]

SKIP_PARTS = {"node_modules", "__pycache__", ".venv", "dist", "tests", "test"}

# String literals in Python and TS/TSX. Good enough to catch prose without
# needing a parser, and it deliberately ignores comments — a comment explaining
# why we avoid a word must be allowed to contain the word.
STRING_LITERAL = re.compile(
    r'"""(?P<triple>.*?)"""|"(?P<double>(?:[^"\\\n]|\\.)*)"|\'(?P<single>(?:[^\'\\\n]|\\.)*)\'',
    re.DOTALL,
)


def iter_source_files():
    for directory in SOURCE_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.suffix not in (".py", ".ts", ".tsx"):
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            yield path


def offending_strings(path: Path, banned: List[str]) -> List[Tuple[str, str]]:
    """Return (word, snippet) for every banned word inside a string literal."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    hits: List[Tuple[str, str]] = []

    for match in STRING_LITERAL.finditer(text):
        literal = next(
            (g for g in (match.group("triple"), match.group("double"),
                         match.group("single")) if g),
            None,
        )
        if not literal:
            continue
        # A docstring is documentation, not user-facing copy.
        if match.group("triple"):
            continue
        lowered = literal.lower()
        for word in banned:
            # Word boundaries, not substrings: "provenance" contains
            # "proven", and provenance is exactly the vocabulary this
            # project should be using. A substring match would ban the
            # right word for the wrong reason.
            # Raw string: a plain "\b" is a backspace character, not a regex
            # word boundary, and the pattern would silently never match.
            pattern = r"\b" + re.escape(word) + r"\b"
            found = re.search(pattern, lowered)
            if found:
                start = found.start()
                snippet = literal[max(0, start - 40) : start + 60].strip()
                hits.append((word, snippet))
    return hits


def test_no_accusatory_language_in_user_facing_strings():
    """The system describes patterns. It never describes a person."""
    failures = []
    for path in iter_source_files():
        for word, snippet in offending_strings(path, ACCUSATORY):
            failures.append(f"{path.relative_to(REPO_ROOT)}: '{word}' in “{snippet}”")

    assert not failures, (
        "Accusatory language found in user-facing strings:\n  "
        + "\n  ".join(failures)
    )


def test_no_certainty_overclaims():
    failures = []
    for path in iter_source_files():
        for word, snippet in offending_strings(path, OVERCLAIM):
            failures.append(f"{path.relative_to(REPO_ROOT)}: '{word}' in “{snippet}”")

    assert not failures, (
        "Language asserting certainty found in user-facing strings:\n  "
        + "\n  ".join(failures)
    )


def test_no_machine_learning_claims():
    """The scoring engine is a rule system. Dressing it up invites a question
    we would then have to answer badly."""
    failures = []
    for path in iter_source_files():
        for word, snippet in offending_strings(path, ML_CLAIMS):
            failures.append(f"{path.relative_to(REPO_ROOT)}: '{word}' in “{snippet}”")

    assert not failures, (
        "Machine-learning claims found in user-facing strings:\n  "
        + "\n  ".join(failures)
    )


def test_lint_actually_catches_something():
    """A lint that cannot fail is not a lint."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write('MESSAGE = "This wallet belongs to a scammer."\n')
        temp_path = Path(handle.name)

    try:
        hits = offending_strings(temp_path, ACCUSATORY)
        assert hits, "the lint failed to catch an obviously accusatory string"
        assert hits[0][0] == "scammer"
    finally:
        temp_path.unlink()


def test_docstrings_are_not_linted():
    """Explaining why a word is avoided must not trip the rule."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write('"""Never call anyone a scammer in output."""\nX = 1\n')
        temp_path = Path(handle.name)

    try:
        assert offending_strings(temp_path, ACCUSATORY) == []
    finally:
        temp_path.unlink()
