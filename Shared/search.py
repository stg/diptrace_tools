"""Case-insensitive, all-word filters with a small wildcard vocabulary."""
import re
from functools import lru_cache


@lru_cache(maxsize=128)
def compile_query(query):
    terms = []
    for word in query.split():
        pattern = []
        index = 0
        while index < len(word):
            char = word[index]
            if char == "\\" and index + 1 < len(word) and word[index + 1] in "*#?\\":
                index += 1
                pattern.append(re.escape(word[index]))
            elif char == "*":
                if not pattern or pattern[-1] != ".*":
                    pattern.append(".*")
            elif char == "#":
                pattern.append(r"\d+")
            elif char == "?":
                pattern.append(".")
            else:
                pattern.append(re.escape(char))
            index += 1
        terms.append(re.compile("".join(pattern), re.IGNORECASE | re.DOTALL))
    return tuple(terms)


def matches_terms(terms, texts):
    values = tuple(texts)
    return all(any(term.search(value) for value in values) for term in terms)
