"""
Text tokenizer for full-text indexing.

Pipeline:
  1. Lowercase
  2. Split on whitespace / newlines only
  3. Strip leading/trailing punctuation from each token
     (dots between non-space characters are preserved — "ecommerce.product"
      stays as one token; "product. " loses the trailing dot)
  4. Drop stop-words and single-character tokens

No stemming — tokens are stored and matched as-is so that dotted namespaces
like "ecommerce.product" round-trip exactly.
"""

# Characters stripped from the start/end of each raw token.
# Internal dots (xxx.yyy) are intentionally NOT in this set.
_STRIP_CHARS = ".,!?;:\"'()[]{}<>@#$%^&*+-=/\\|~`_"

STOP_WORDS = {
    "the", "is", "a", "and", "for", "in", "to", "of",
    "with", "an", "on", "at", "by", "it", "as", "or",
    "be", "this", "that", "are", "was", "were", "not",
}


def tokenize(text: str) -> list:
    """
    Tokenize *text* and return a list of lowercase tokens.

    Rules
    -----
    * Split on any whitespace/newline.
    * Strip leading/trailing punctuation (not dots embedded inside a word).
    * Drop stop-words and single-character tokens.
    * No stemming — tokens are returned verbatim (lowercased).

    Examples
    --------
    >>> tokenize("ecommerce.product")
    ['ecommerce.product']
    >>> tokenize("Hello, world.")
    ['hello', 'world']
    >>> tokenize("Mr. Smith")
    ['mr', 'smith']
    """
    if not text:
        return []
    tokens = []
    for raw in text.lower().split():
        word = raw.strip(_STRIP_CHARS)
        if word and word not in STOP_WORDS and len(word) > 1:
            tokens.append(word)
    return tokens
