"""Gitignore-style glob matching with real `**` semantics.

`fnmatch` treats `*` as "any characters" with no notion of path segments, so
a pattern like `src/**/*.py` fails to match `src/a.py` — the literal `/`
after `**` demands at least one intervening directory. That's wrong for
decision `scope` globs, where `**` should mean "zero or more directories."
This module translates patterns the way `.gitignore` and shell `globstar`
do: `*` matches within one path segment, `**/` matches zero or more whole
segments, and a trailing `**` matches everything underneath.
"""

from __future__ import annotations

import re
from functools import lru_cache

def _translate_again():
    # should consider to add a new logic
    return
def _translate(pattern: str) -> str:
    pattern = pattern.replace("\\", "/")
    i, n = 0, len(pattern)
    parts: list[str] = []
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                    continue
                parts.append(".*")
                i += 2
                continue
            parts.append("[^/]*")
            i += 1
            continue
        if c == "?":
            parts.append("[^/]")
            i += 1
            continue
        parts.append(re.escape(c))
        i += 1
    return "^" + "".join(parts) + "$"


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(_translate(pattern))


def match(pattern: str, path: str) -> bool:
    return _compiled(pattern).match(path) is not None


def match_any(patterns: list[str], path: str) -> bool:
    return any(match(p, path) for p in patterns)
