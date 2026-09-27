"""Hard filters in a query: IMDb rating, release year, runtime. See D-027.

Embeddings can't compare numbers ("IMDb above 8.5" matched Fast X, rated 5.7), and the
rating isn't in the blob anyway. So numbers are parsed out of the query here and
applied as a Chroma `where` on metadata; the rest of the query is what gets embedded:

    "action movies with more 8.5 above imdb"
        -> semantic "action movies", Filters(min_rating=8.5)

Rules-based on purpose: instant (step 1 has no LLM), deterministic, and testable.
Bare years are ignored ("2012" is also a movie title); a year needs a direction
word ("after 2010") or a decade ("90s").
"""

import re

from pydantic import BaseModel

RATING_WORDS = {"imdb", "rating", "ratings", "rated", "star", "stars", "score"}
UP_WORDS = {"above", "over", "more", "greater", "least", "min", "minimum", "plus", "upar", "zyada", "jyada", "+"}
DOWN_WORDS = {"below", "under", "less", "max", "maximum", "kam", "neeche", "niche", "within"}
AFTER_WORDS = {"after", "since", "baad", "newer"}
BEFORE_WORDS = {"before", "until", "till", "pehle", "older"}
HOUR_WORDS = {"hour", "hours", "hr", "hrs", "h", "ghanta", "ghante"}
MINUTE_WORDS = {"min", "mins", "minute", "minutes"}
# Glue words dropped from the semantic query when they sit inside a filter phrase
FILLER = {"with", "than", "and", "or", "of", "a", "an", "se", "ke", "ki", "ka", "is", "wali", "wale", "between",
          "at", "long", "to", "the"}
# Each kind of phrase only claims its own words, so "under 2 hours imdb 7+" splits cleanly
RATING_PHRASE = RATING_WORDS | UP_WORDS | DOWN_WORDS | FILLER
YEAR_PHRASE = AFTER_WORDS | BEFORE_WORDS | FILLER | {"s"}
RUNTIME_PHRASE = HOUR_WORDS | MINUTE_WORDS | UP_WORDS | DOWN_WORDS | FILLER
WINDOW = 4  # tokens on each side of a number that can belong to its phrase

_TOKEN = re.compile(r"\d+(?:\.\d+)?s?\+?|[a-z]+|[+]")


class Filters(BaseModel):
    min_rating: float | None = None
    max_rating: float | None = None
    min_year: int | None = None
    max_year: int | None = None
    max_runtime: int | None = None  # minutes
    min_runtime: int | None = None

    def active(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


def parse_filters(query: str) -> tuple[str, Filters]:
    """(semantic query, filters). The semantic query is the input minus the filter phrases;
    if nothing is left ("imdb 9+"), it's "a good movie"."""
    matches = list(_TOKEN.finditer(query.lower()))
    tokens = [m.group() for m in matches]
    f = Filters()
    used: set[int] = set()

    for i, tok in enumerate(tokens):
        if not tok[0].isdigit():
            continue
        lo, hi = max(0, i - WINDOW), min(len(tokens), i + WINDOW + 1)
        # Words an earlier phrase already used don't count: in "under 2 hours imdb 7+",
        # "under" belongs to the runtime, not the rating.
        near = {tokens[j] for j in range(lo, hi) if j != i and j not in used}
        plus = tok.endswith("+")
        num = tok.rstrip("+")
        claimed: set[str] = set()  # the word kind this number's phrase claims

        if num.endswith("s") and num[:-1].isdigit():  # decade: 90s, 1990s, 2000s
            start = int(num[:-1])
            start = start + 1900 if start < 100 else start
            if 1900 <= start <= 2090 and start % 10 == 0:
                f.min_year, f.max_year = start, start + 9
                claimed = YEAR_PHRASE
        elif num.isdigit() and len(num) == 4 and 1900 <= int(num) <= 2099:
            year = int(num)
            before = set(tokens[lo:i])
            if near & AFTER_WORDS or plus:
                f.min_year = year
                claimed = YEAR_PHRASE
            elif near & BEFORE_WORDS:
                f.max_year = year - 1
                claimed = YEAR_PHRASE
            elif "between" in before and f.min_year is None:
                f.min_year = year
                claimed = YEAR_PHRASE
            elif f.min_year is not None and f.max_year is None and "and" in before:
                f.max_year = year  # "between 2000 and 2010"
                claimed = YEAR_PHRASE
        else:
            value = float(num)
            after = set(tokens[i + 1:hi])
            if after & HOUR_WORDS or after & MINUTE_WORDS:
                minutes = round(value * 60) if after & HOUR_WORDS else round(value)
                if near & UP_WORDS - {"min"} or plus:
                    f.min_runtime = minutes
                else:  # "under 2 hours", "2 ghante se kam", or just "2 hours"
                    f.max_runtime = minutes
                claimed = RUNTIME_PHRASE
            elif 0 < value <= 10 and near & RATING_WORDS:
                if near & DOWN_WORDS and not plus:
                    f.max_rating = value
                else:  # "8.5+", "above 8", "rated 8", "8 se upar"
                    f.min_rating = value
                claimed = RATING_PHRASE

        if claimed:
            used.add(i)
            used.update(j for j in range(lo, hi) if tokens[j] in claimed)

    if not used:
        return query, f
    # Cut the filter phrases out of the original text, keeping its casing and punctuation
    semantic, last = "", 0
    for j in sorted(used):
        semantic += query[last:matches[j].start()]
        last = matches[j].end()
    semantic += query[last:]
    semantic = re.sub(r"\s+", " ", semantic).strip(" ,.-")
    return (semantic or "a good movie"), f


def to_where(f: Filters) -> dict | None:
    """Chroma metadata filter. Movies without a value (no rating) never match a bound on it."""
    conds = []
    for field, key, op in [
        ("min_rating", "imdb_rating", "$gte"), ("max_rating", "imdb_rating", "$lte"),
        ("min_year", "year", "$gte"), ("max_year", "year", "$lte"),
        ("min_runtime", "runtime", "$gte"), ("max_runtime", "runtime", "$lte"),
    ]:
        value = getattr(f, field)
        if value is not None:
            conds.append({key: {op: value}})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def describe(f: Filters) -> str:
    """Short label for the UI: "IMDb 8.5+ · 1990–1999 · under 2h"."""
    parts = []
    if f.min_rating is not None and f.max_rating is not None:
        parts.append(f"IMDb {f.min_rating:g}–{f.max_rating:g}")
    elif f.min_rating is not None:
        parts.append(f"IMDb {f.min_rating:g}+")
    elif f.max_rating is not None:
        parts.append(f"IMDb up to {f.max_rating:g}")
    if f.min_year is not None and f.max_year is not None:
        parts.append(f"{f.min_year}–{f.max_year}")
    elif f.min_year is not None:
        parts.append(f"{f.min_year} or later")
    elif f.max_year is not None:
        parts.append(f"{f.max_year} or earlier")
    if f.max_runtime is not None:
        parts.append(f"up to {f.max_runtime} min")
    if f.min_runtime is not None:
        parts.append(f"{f.min_runtime} min or longer")
    return " · ".join(parts)
