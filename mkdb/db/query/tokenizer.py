"""
Text tokenizer for full-text indexing.

Pipeline: lowercase → strip non-alphanumeric (keep spaces) → split on whitespace
         → remove stop-words → apply simple suffix-stripping stemmer.
"""

import re

STOP_WORDS = {
    "the", "is", "a", "and", "for", "in", "to", "of",
    "with", "an", "on", "at", "by", "it", "as", "or",
    "be", "this", "that", "are", "was", "were", "not",
}


def _stem(word: str) -> str:
    """
    Minimal suffix-stripping stemmer (no NLTK dependency).
    Applies the most common English suffix rules in order.
    """
    if len(word) <= 3:
        return word
    for suffix, replacement in [
        ("ational", "ate"), ("tional", "tion"), ("enci", "ence"),
        ("anci", "ance"), ("izer", "ize"), ("ising", "ise"),
        ("izing", "ize"), ("ness", ""), ("ment", ""), ("ful", ""),
        ("less", ""), ("ings", "ing"), ("ing", ""), ("edly", ""),
        ("edly", "ed"), ("edly", ""), ("ed", ""), ("er", ""),
        ("ly", ""), ("ies", "i"), ("ied", "i"), ("es", "e"),
        ("s", ""),
    ]:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def tokenize(text: str) -> list:
    """
    Tokenize text into a list of stems.
    Returns an empty list for empty/None input.
    """
    if not text:
        return []
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    words = cleaned.split()
    return [_stem(w) for w in words if w not in STOP_WORDS and len(w) > 1]
