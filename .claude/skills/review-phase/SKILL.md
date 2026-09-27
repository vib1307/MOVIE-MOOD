---
name: review-phase
description: Review the code the user wrote for a MovieMood build phase against its checkpoint in docs/PLAN.md. Mentor-style — explain mistakes and give hints, not rewrites.
argument-hint: <phase-number>
---

# Review phase $ARGUMENTS

The user is learning. They wrote this code themselves. Your job is to be a mentor, not to finish the work.

1. Read `docs/PLAN.md` and find Phase $ARGUMENTS: its goal, the files it names, and its ✅ checkpoint.
2. Read the files that phase touches under `src/moviemood/`, `scripts/`, and the config files (`requirements.txt`, `pyproject.toml`, `.gitignore`). Never read `.env`.
3. If the checkpoint has a safe, read-only command (imports, `ollama list`, `git status`), run it and report the result.
4. Report in this format, briefly:
   - **Checkpoint:** pass / fail / can't verify (and why)
   - **Bugs:** things that are wrong or will break later. For each: where (`file:line`), what, why it matters.
   - **Improvements:** idiomatic Python or design points (e.g. `core/` must not import FastAPI/Gradio). Maximum 3.
   - **Hints:** how to fix each bug. Point the direction and name the API or concept. Don't paste full corrected code unless the user asks.
   - **Next:** ready for Phase N+1, or what's still missing.
5. If the phase passes, remind the user to update "Current phase" in `CLAUDE.md`.
