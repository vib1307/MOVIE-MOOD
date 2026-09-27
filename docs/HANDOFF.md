# Session handoff

If a session ends suddenly, start the next one from here. Update this file at every checkpoint, and clear sections once they're done.

_Last updated: 2026-09-27_

## Where things stand
- **Phase 6 (Gradio UI) + 6.5 (lazy ingestion) are built but not committed.** 74 tests pass. Details: D-023 to D-027 in `docs/DECISIONS.md` (D-026: qwen default; D-027: rating/year/runtime filters parsed from the query).
- The catalog is now 505 films (500 discover + 10 lazy ids, some overlapping). The index was rebuilt. Lazy ingest also handles moods (TMDB discover by genre + keyword).
- Done this session:
  - Lazy ingestion (actors + directors).
  - `added_ids.json` merge in `fetch_movies.py`.
  - A "Directed by" blob line (index rebuilt, 25 films).
  - Nocturne UI from the user's Claude Design mock.
  - The "Why this pick" panel (`core/explain.py`).
- Design source: `~/Desktop/Mood-Based Movie Recommendation App_files/Movie Mood.dc.html` (+ `styles.css`, `moods.js`).

## Current task: Phase 7 deploy (plan approved 2026-09-27)
Plan: `~/.claude/plans/nice-lets-think-of-tranquil-allen.md`. User's choices:
- **Hetzner CAX31** (ARM, 8 vCPU / 16 GB, ~€13/mo)
- **local qwen2.5:7b**
- **DuckDNS** subdomain + Let's Encrypt

Done: Phase 6/6.5 committed + pushed by the user (`fd7e835`). `deploy/` + `requirements.lock` written, D-028 logged (not committed yet).
Next: the user follows `deploy/README.md` §1 (Hetzner account + CAX31 + firewall, DuckDNS) and shares the server IP + subdomain, then §2–7 together. Afterwards, fill in the step-2 latency + nginx verification in D-028.

## Next
1. The user reviews the UI at `uvicorn moviemood.api.main:app --reload` → `/`.
2. `/review-phase 6` (and 6.5), then commit, then Phase 7 deploy.
3. **qwen2.5:7b is now the default LLM (D-026).** Phase 7 must size EC2 for it (~16 GB), or set `LLM_MODEL=llama3.2`.
4. Quality gap (D-025 known limits): the 3B judge passes weak fits (Kal Ho Naa Ho for "rona nahi chahiye"), and word overlap hurts retrieval ("kuch" → Kuch Kuch Hota Hai). Candidates: the bigger-LLM test (D-018), or a stricter judge prompt.
