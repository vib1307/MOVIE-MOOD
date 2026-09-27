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

## D-008 · Phase 1 · OMDb client (`ingest/omdb.py`)
**Decision:** `OMDbClient.fetch(imdb_id)` caches raw responses in `data/cache/omdb/{imdb_id}.json`, **including "not found" results**. `parse_rating()` is separate and turns `"N/A"`, a missing rating, or `Response: "False"` into `None`. A 401 containing "limit" raises `OMDbLimitReached`; any other 401 raises `OMDbError`. Invalid JSON is treated as "not found". `_make_session`/`_get` are copied from `tmdb.py` for now.
**Why:** OMDb has several quirks:
- It returns HTTP 200 for errors.
- Ratings are strings.
- Its error text sometimes breaks its own JSON (unescaped quotes for malformed ids; seen live).
- The free tier is 1000 requests/day, and retrying a daily limit is pointless, so the run should stop. Caching negative results means missing movies don't burn quota on every re-run.
- Fetching and parsing are split so the parsing is testable offline.
**Revisit if:** a third HTTP client appears (then extract the session and error handling to `ingest/http.py`), or cached "not found" entries need a refresh (delete `data/cache/omdb/`).

## D-009 · Phase 1 · `scripts/fetch_movies.py` behavior
**Decision:** `--limit` flag (default 20). A movie whose TMDB call fails is skipped and counted. When the OMDb daily limit is hit, the run **continues without ratings** instead of aborting. `movies.json` is rebuilt from the caches on every run and written atomically (temp file, then rename).
**Why:** One bad movie shouldn't kill a 500-movie run. Because everything is cached, a re-run is cheap (1.7s vs 14s cold for 20 movies) and fills in missing ratings the next day. Atomic write means a crash never leaves a half-written JSON file for Phase 3 to choke on.
**Revisit if:** runs get long enough that we want progress saved mid-run (not needed while caches exist).

## D-010 · Phase 1 · Skip movies released in the last 90 days
**Decision:** `discover_ids(min_age_days=90)` adds `primary_release_date.lte = today - 90 days` to discover. Still sorted by `popularity.desc`. The cutoff is relative to today, so it moves forward on future runs.
**Why:** The first run had 12/20 movies from 2026. The 7 newest (11–81 days old) had few votes (500–800), and one had no IMDb rating. After the change: newest release is 2026-06-24 and 20/20 have a rating. 6 months was considered and rejected: it would also drop solid titles (Toy Story 5: 27 keywords; Obsession: 5.7k votes).
**Revisit if:** the index feels dated, or new releases matter for the product.

## D-011 · Phase 1 · Fixes from `/review-phase 1`
**Decision:**
- Cache writes go through `ingest/cache.py`: `write_json` is atomic (temp file, then rename). `read_json` deletes a corrupt file and returns `None` so the entry is refetched.
- `fetch_movies.py` saves the chosen ids to `data/cache/discover_ids.json` together with the **requested** `limit`, and reuses them. `--refresh` forces a new discover; a larger `--limit` than the saved one also rediscovers. (Comparing against `len(ids)` was a bug caught in review: after dedup, discover can return fewer ids than asked, which would rediscover on every run at scale.)
- The per-movie `except` also catches `KeyError` and `pydantic.ValidationError`. `to_movie` uses `x.get(k) or {}` so fields that are present but null don't crash, and TMDB `_get` turns non-JSON 200 responses into `TMDBError`.
**Why:** A crash mid-write used to leave a truncated cache file that crashed every later run. Discover results change daily (popularity, moving cutoff), so "re-run tomorrow to fill ratings" would have fetched a different list. One malformed TMDB record could still kill the run.
**Revisit if:** the list should follow current popularity. Use `--refresh`.

## D-012 · Phase 1 · Redact API keys from urllib3 logs (corrects D-006)
**Decision:** `ingest/http.py` adds a logging filter that rewrites `api_key=…`/`apikey=…` to `***` in the `urllib3.connectionpool` and `urllib3.util.retry` loggers. Both clients install it on init.
**Why:** D-006's claim that the key "can't leak into logs" was wrong. `_get()` hides the key in its own errors, but urllib3 logs its own WARNING with the full URL on every retry. This happened live during testing on 2026-09-27 (a connection reset → retry warning printed the TMDB key). The key went to terminal output only, not into any file or commit. **The TMDB key should be rotated.**
**Revisit if:** we switch TMDB to the v4 bearer token (header instead of URL), which removes the key from URLs entirely.

---

## Open questions
- ~~**Recency skew**~~: resolved by D-010.
- **Thin keywords:** not about age. Colony (3 keywords) and The Death of Robin Hood (5) survive the cutoff. Check blob quality in Phase 2; a possible fix is a minimum keyword count.
- **Dedup shortfall at scale:** `discover_ids` fetches a fixed number per genre, so after dedup a large `limit` (e.g. 500) can return fewer ids. Fix: keep paging until the number of unique ids reaches `limit`.
