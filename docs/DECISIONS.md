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

## D-013 · Phase 2 · Semantic blob design (from `/grill-me`)
**Decision:**
- **Blob fields:** title, genres, top 3 cast (`Starring:`), overview, and **all** keywords, as labeled lines. Year, runtime, rating, and poster stay metadata only.
- **No nomic prefix in the blob.** A small `OllamaEmbeddings` wrapper in `core/vectorstore.py` (Phase 3) adds `search_document:` / `search_query:` automatically. The installed langchain-ollama 1.1.0 has no prefix option.
- **`core/semantic_text.py`:** pure functions `build_document_text(movie) -> str` and `to_metadata(movie) -> dict` (flat scalars, lists joined into strings, `None` keys **omitted**; resolves the D-007 follow-up).
- **Checkpoint:** print 3 blobs + `scripts/try_blobs.py`, which embeds all blobs in memory and prints the top 3 for a few mood queries.
- **Not yet:** a Themes/Tone keyword split, or LLM mood tags. Add them only if the ranking test shows noise or thin movies ranking badly.
**Why:**
- Only meaning-bearing text helps embeddings. Cast was added after the user's "romantic Shah Rukh Khan" example: shared names boost similarity at ~10 tokens of cost. Exact actor matching needs a v2 metadata filter, since embeddings are fuzzy on names.
- A keyword cap would drop the mood words, because TMDB lists them last (`feelgood`, `optimism`).
- A clean blob keeps the prefix out of Chroma's `page_content` and out of the Phase 4 LLM prompt.
- A placeholder like `-1` for a missing rating would break `rating >= 7` filters.
**Revisit if:** the ranking test fails for noisy or thin movies.

## D-014 · Phase 1 (revisit) · Include Indian films
**Decision:** a second discover pass with `with_original_language` in `hi`, `ta`, `te`, for **25%** of `limit` (parameter `indian_share=0.25`), with `min_votes=100` for that slice, mixed into the main list.
**Why:** The dataset was Hollywood-only (global popularity + en-US), so queries like "romantic Shah Rukh Khan" had nothing to match. South Indian films (RRR, Baahubali, Pushpa) are hugely popular. Indian films get fewer TMDB votes, so 300 would cut good films.
**Revisit if:** Indian overviews and keywords turn out too thin (then LLM mood tags, D-013), or the share needs tuning after the first run.
**Implemented:** `TMDBClient.discover_ids(indian_share=0.25, indian_languages=("hi","ta","te"), indian_min_votes=100)`. The Indian ids are spread evenly through the list (`_spread`), so any prefix keeps the ratio. `Movie` gained `original_language` for future language filters. First run: 3 Idiots, Leo, RRR, DDLJ, Maharaja. Their keyword counts are thin (6–7 for three of them).
**Note:** changing discover parameters requires `fetch_movies.py --refresh`, because the saved id list is keyed only by `limit`.

## D-015 · Phase 2 · Checkpoint accepted; LLM mood tags deferred
**Decision:** Phase 2 passes with known limits. No LLM mood tags yet; rerun `scripts/try_blobs.py` after scaling to 300–500 movies and decide then.
**Why:** 4 of 6 test queries were good, including the cast-driven SRK query. The two failures have different causes. Negation ("no sad ending" → Titanic) can't be fixed in the blob; it's the Phase 4 LLM re-rank's job. Thin keywords (Colony) are one data point out of 20 movies, too few to justify a per-movie LLM step.
**Revisit if:** at 500 movies, thin-keyword films still miss obvious mood queries.

## D-016 · Phase 3 · Vector store design (from `/grill-me`)
**Decision:**
- `NomicEmbeddings(OllamaEmbeddings)` subclass in `core/vectorstore.py` adds `search_document:` / `search_query:` prefixes. Its `embed_query` calls `super().embed_documents` **directly**, because the parent's `embed_query` calls `self.embed_documents` and would otherwise double-prefix the query (found by reading langchain-ollama 1.1.0's source).
- **Cosine** distance: `collection_configuration={"hnsw": {"space": "cosine"}}` (Chroma's default is l2).
- **Full rebuild** on every `build_index`: delete the collection, re-embed everything. Ids = `str(tmdb_id)`.
- `get_vectorstore()` (`@lru_cache`, shared by the API and UI) and `build_index(movies)` live in core; `scripts/build_index.py` is a thin CLI.
- At runtime, **Chroma finds** and **`movies.json` provides the full details** (`{tmdb_id: Movie}`). Deploy needs both files.
**Why:**
- A subclass is ~10 lines.
- Cosine distances map directly to `try_blobs.py` scores (distance = 1 − score).
- Upsert-only would leave stale movies (e.g. Forrest Gump after the Indian slice) that the API can't find details for. Sync would leave old blob formats behind.
- One place for store setup means the API and script can't drift apart.
**Verified:** all 6 test queries return the same top 3 as `try_blobs.py`, with distance = 1 − score (DDLJ 0.714 → 0.286). A re-run keeps count = 20.
**Known limit:** the `PLAN.md` checkpoint query "cozy feel-good" returns Interstellar, Practical Magic, Toy Story 5, all at ~0.54 distance: weak separation. The storage is correct; this is the Phase 2 mood-signal limit (D-015), left for the Phase 4 LLM re-rank and the 500-movie retest.

## D-017 · Phase 4 · Recommender design (from `/grill-me`) + first results
**Decision:**
- `core/recommender.py` › `recommend(query, k=5, candidates=15, rerank=True)`: Chroma top 15 → llama3.2 picks by **short number** `[1]..[15]` (not TMDB id) and writes "why".
- `ChatOllama(format=<JSON schema>, temperature=0)`. Schema requires at least one pick (`minItems: 1`).
- Validation: drop out-of-range numbers and duplicates, then fill empty slots in retrieval order with a template "why" (last 3 keywords, i.e. the mood words). Any LLM failure means pure retrieval order. Each result has `source: "llm" | "fallback"`.
- Retrieval errors (Ollama down) are not caught; Phase 5 maps them to a 503.
- Returns `Recommendation` objects (in `core/models.py`); details come from `core/catalog.py` › `get_catalog()` (`movies.json`).
- `rerank=False` fast mode (no LLM, <1s).
- Checkpoint: `scripts/try_recommend.py`, 5 queries + 1 Hinglish probe, retrieval vs re-ranked side by side.
**Why:**
- In a pre-test, llama3.2 mistyped a 7-digit TMDB id (1084242 for 1084244); short numbers avoid that.
- The schema forces the JSON structure; validation still guards the ids.
- temperature=0 makes runs reproducible.
- The fallback always returns k results.
- Fast mode is a backup if CPU-only EC2 is too slow.
**Found while building:** without `minItems`, llama3.2 returned `{"picks": []}` for every query (valid JSON, zero picks, so silent 100% fallback).
**First results (20 movies, ~5s per query on the Mac):** the plumbing works (all picks valid, every result has a why), but **quality is mixed; the 3B model's re-rank is not reliably better than retrieval:**
- ✅ Horror: Minions & Monsters removed, Colony surfaced. Hinglish "kuch halka sa, rona nahi chahiye" → 3 Idiots, DDLJ (good, but Colony at #4).
- ⚠️ "feel-good, no sad ending": Titanic moved from #3 to #4 but stayed in, and its why says "tragic twist, but ultimately feel-good".
- ❌ "romantic Shah Rukh Khan": Maharaja #1 over DDLJ, plus Interstellar and Blade Runner with an invented "romantic storyline".
- ❌ "funny animated for kids": 3 Idiots #1, Zootopia 2 dropped.
- Some "why"s just copy the overview (horror query) and aren't explanations.
- Template why can show non-English TMDB keywords (Maharaja: 因果报应).
**Status:** Phase 4 checkpoint **not passed on quality** yet. The next step is a design choice (see Open questions).

## D-018 · Phase 4 · LLM demotes misfits instead of re-ranking (replaces D-017's re-rank)
**Decision:** Final order = retrieval order. The LLM judges the top `2*k` candidates (`fits` + `why`), and the output is ordered in three tiers:
1. fits, plus unjudged movies inside the window (retrieval order)
2. judged misfits: `source="demoted"`, template why
3. candidates beyond the window

Nothing is removed, and the LLM never reorders freely.
**Why:** Three designs were tested on the same 6 queries:
- **Free re-rank (D-017)** made correct retrieval results worse (Maharaja over DDLJ for SRK; 3 Idiots over Toy Story for kids).
- **Hard veto** fixed the negation cases (Titanic out for "no sad ending", Minions out of horror), but the 3B model over-vetoed ("Zootopia 2 is not primarily a comedy"). The gaps were then filled from ranks 11–15, putting Colony, a zombie film, into the kids' results.
- **Demotion** keeps the wins and makes a wrong veto cheap.

Results with demotion:
- feel-good: DDLJ, Practical Magic, RRR; **Titanic 3→5**
- horror: Resident Evil, **Colony ↑**, **Minions 3→4**
- SRK: **DDLJ #1**
- kids: identical to retrieval
- sci-fi: identical to retrieval
- Hinglish: DDLJ, 3 Idiots, Practical Magic (better)

Latency is ~7–9s on the Mac.
**Known limits:** Titanic is still in the feel-good top 5 (at #5). Demoted movies show the neutral "Close match for your mood: …" template, which reads oddly for a demoted Titanic. The LLM's reasons for demotions are only logged.
**Revisit if:** the bigger-model test (open question) shows the LLM can be trusted with more (removal or reordering).

---

## Open questions
- ~~**Recency skew**~~: resolved by D-010.
- **Phase 2 ranking test (first run, 20 movies), `scripts/try_blobs.py`:**
  - ✅ "romantic Shah Rukh Khan" → DDLJ, clear winner (0.71 vs 0.61): the cast-in-blob decision works. "epic space adventure" → Interstellar; "funny animated for kids" → Toy Story 5 / Minions / Zootopia 2.
  - ❌ "feel-good, no sad ending" → **Titanic #1**. Embeddings don't understand negation ("no sad ending" ≈ "sad ending"). That's the Phase 4 LLM re-rank's job, not the blob's.
  - ❌ "dark scary horror" → Minions & Monsters #3 (word overlap on "Monsters"), and Colony (a real horror film, 3 keywords) is missing from the top 3. This is the first evidence that thin keywords hurt.
  - Scores are tightly clustered (0.49–0.50 for the feel-good top 3), so separation is weak.
- **Thin keywords:** not about age. Colony (3 keywords) and The Death of Robin Hood (5) survive the cutoff. Check blob quality in Phase 2; a possible fix is a minimum keyword count.
- **Dedup shortfall at scale:** `discover_ids` fetches a fixed number per genre, so after dedup a large `limit` (e.g. 500) can return fewer ids. Fix: keep paging until the number of unique ids reaches `limit`.
- **Bigger LLM (D-018):** test `qwen2.5:7b` (~4.7 GB download) with the same `scripts/try_recommend.py` queries: can it veto accurately, or even re-rank? Trade-off: ~2–3x slower, ~16 GB RAM on EC2. Deferred by the user; revisit after Phase 5–6.
