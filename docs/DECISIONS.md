# Decision log

The *why* behind MovieMood's design choices. Git records **what** changed; this file records **why**.
Add new entries at the bottom. Don't edit old entries: if a decision changes, add a new entry that says it replaces the old one.

Format: **Decision** / **Why** / **Revisit if**.

---

## D-001 · Phase 0 · Phase numbering + working style
**Decision:** Phase 0 = environment, Phase 1 = fetch data (matches `PLAN.md`; `CLAUDE.md` had them mislabeled). Claude writes code when the user asks ("code it"); otherwise gives hints and scaffolds.
**Why:** The mismatch made "current phase" ambiguous. Hints-only was slowing things down when the user just wanted code.
**Revisit if:** the user wants to go back to writing everything themselves.

## D-002 · Phase 1 · One `Movie` record type in `core/models.py`
**Decision:** A Pydantic `Movie` model is the single normalized record. Only `tmdb_id` and `title` are required; everything else has a default. `ingest/` imports it from `core/`.
**Why:** One source of truth shared by ingest, the vector store, and the API (like a shared TS interface, but validated at runtime). Lenient defaults so patchy TMDB/OMDb data still loads; missing id/title fails loudly.
**Revisit if:** the API needs a different shape (then add schemas in `api/schemas.py`; don't bend the core model).

## D-003 · Phase 1 · Round-robin discover across genres
**Decision:** `TMDBClient.discover_ids(limit)` runs `/discover/movie` per genre (8 genres, `popularity.desc`, `vote_count.gte=300`), interleaves the lists, dedupes, and caps at `limit`. `limit` is a parameter: 20 for Sprint A, 300–500 later.
**Why:** Popularity sort alone clusters in one genre, which means narrow mood coverage. The vote floor filters out obscure titles with thin overviews.
**Revisit if:** results skew too recent (see Open questions).

## D-004 · Phase 1 · Raw per-movie cache on disk
**Decision:** Each raw TMDB details response is saved to `data/cache/tmdb/{id}.json` (gitignored) and reused on re-runs.
**Why:** A crash at movie 340 shouldn't cost 340 API calls again. OMDb's free tier is capped at 1000 requests/day, so the same pattern will apply there.
**Revisit if:** we need fresh data. Delete the cache dir to force a refetch.

## D-005 · Phase 1 · `requests.Session` + urllib3 `Retry`
**Decision:** One session with `Retry(total=5, backoff_factor=1, status_forcelist=[429,500,502,503,504], respect_retry_after_header=True)` and a 10s timeout per request.
**Why:** `requests` is already a dependency. The session reuses connections, and the retry handles rate limits and flaky 5xx errors without hand-written loops.
**Revisit if:** we go async (then switch to `httpx`).

## D-006 · Phase 1 · TMDB auth detection + no key in errors
**Decision:** If the key starts with `eyJ` (a v4 JWT), send it as a Bearer header; otherwise send it as the `api_key` query param. `_get()` raises `TMDBError` with only the path and status, `from None`.
**Why:** Claude may not read `.env`, so the code handles either key type. `requests` error messages include the full URL, which would put a v3 key in logs and tracebacks.
**Revisit if:** never, unless TMDB changes its auth.

## D-007 · Phase 1 · Missing data becomes `None`, don't drop the movie
**Decision:** OMDb `"N/A"`, runtime `0`, and an empty `release_date` all map to `None`.
**Why:** Rating, runtime, and year are metadata for v2 filters, not part of the embedding, so a movie without them is still useful for mood search.
**Revisit if:** Phase 3. Chroma metadata can't hold `None`, so we'll have to decide then whether to omit the key or use a sentinel value.

---

## Open questions
- **Recency skew:** `popularity.desc` pulls in very new releases, which have few votes and maybe no IMDb rating yet. Check once `data/movies.json` exists; fixes would be a higher `min_votes` or `vote_count.desc`.
