# Session handoff

If a session ends suddenly, start the next one from here. Update this file at every checkpoint, and clear sections once they're done.

_Last updated: 2026-09-28_

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

## Current task: Phase 7 deploy on Hetzner CPX22 + OpenAI (D-033, 2026-09-28)
- **Server created (D-035, 2026-09-30):** Hetzner **CX23**, Helsinki, `root@89.167.119.84`, Ubuntu 26.04.1. SSH key login works. Repo cloned to `/opt/moviemood` (§2 done). **LIVE: https://moviemood.duckdns.org** (2026-09-30). §3–7 done: setup.sh (26.04 fix), data rsync, `.env` by the user, index 3m17s (515 films), Let's Encrypt (auto-renew timer, expires 2026-12-29), rate limit OK. Step 2 ≈ 4.5s with OpenAI (logged in D-033).
  Reboot test passed (green ~40s after reboot). The user checked the UI in the browser and took a Hetzner snapshot. **Phase 7 done.**
  Deploy new code: Day-2 table in `deploy/README.md` (`git pull` + `setup.sh` + restart).
- **Oracle is abandoned:** "Out of capacity" for A1, and the region has only one AD.
- **Code done (not committed):**
  - `LLM_PROVIDER` switch in `core/llm.py`
  - 10 films per search (UI + API)
  - health/warm-up per provider
  - `setup.sh` pulls only nomic
  - README is Hetzner-first
  - `pytest`: 83 pass. The real Ollama path was checked.
  - D-034: the UI shows a loader, then draws results once after the LLM (no more draft list that swaps). 84 pass.
- **Next:**
  1. The user adds `LLM_PROVIDER=openai` + `OPENAI_API_KEY` to the local `.env` and runs `python scripts/try_recommend.py` (check quality + speed vs qwen). Then the UI at `/`.
  2. Commit + push.
  3. README §1: OpenAI budget, Hetzner CPX22 + firewall, DuckDNS.
  4. §2–7 on the server together.
  5. Log the step-2 latency in D-033.

## Earlier: Current task: Phase 7 deploy (plan approved 2026-09-27)
Plan: `~/.claude/plans/nice-lets-think-of-tranquil-allen.md`. User's choices:
- **Hetzner CAX31** (ARM, 8 vCPU / 16 GB, ~€13/mo)
- **local qwen2.5:7b**
- **DuckDNS** subdomain + Let's Encrypt

Done: Phase 6/6.5 committed + pushed by the user (`fd7e835`). `deploy/` + `requirements.lock` written, D-028 logged (not committed yet).
Next: the user follows `deploy/README.md` §1 (Hetzner account + CAX31 + firewall, DuckDNS) and shares the server IP + subdomain, then §2–7 together. Afterwards, fill in the step-2 latency + nginx verification in D-028.

### Server status (2026-09-27, evening)
- **Hetzner:** account verified. **$25 was paid at verification; use it later** (it should be account credit that pays future invoices; confirm in Console → Billing). No server created yet.
- **Blocker:** "Cost-Optimized" (ARM CAX31) is disabled for the account (tried fs). The AMD 16 GB option (CPX42) is ~$82/mo, too much. Pending: try nbg/hel.
- **Oracle Always Free, checked on docs.oracle.com:** **2 OCPU / 12 GB** A1 (not 4/24), 200 GB block storage, free for the life of the account in the home region. The card is for verification only (temporary hold, no charge unless the account is upgraded). Idle instances (7 days <20% CPU/net/mem) may be reclaimed, and "out of host capacity" is possible.
- **Decided (D-032): Oracle Always Free**, local qwen, DuckDNS. `deploy/` adapted for Oracle (the `ubuntu` user, iptables, swap). Next: the user does README §1 (signup, VM, VCN ingress 80/443, DuckDNS) and shares the IP + subdomain, then §2–7 together.
- Parked for later: show ≥50 / all matching films; AWS serverless as a possible Phase 8.

## Next
1. The user reviews the UI at `uvicorn moviemood.api.main:app --reload` → `/`.
2. `/review-phase 6` (and 6.5), then commit, then Phase 7 deploy.
3. **qwen2.5:7b is now the default LLM (D-026).** Phase 7 must size EC2 for it (~16 GB), or set `LLM_MODEL=llama3.2`.
4. Quality gap (D-025 known limits): the 3B judge passes weak fits (Kal Ho Naa Ho for "rona nahi chahiye"), and word overlap hurts retrieval ("kuch" → Kuch Kuch Hota Hai). Candidates: the bigger-LLM test (D-018), or a stricter judge prompt.
