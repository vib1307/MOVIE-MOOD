"""Open question from D-018: is a bigger LLM a better judge? Scores judges on the same candidates.

Each model judges exactly the same 8 retrieved candidates per query (the window for
k=4), with the recommender's own prompt and JSON schema. Hand labels mark the clear-cut
cases only: films that obviously fit, and films that obviously go against the request.
Unlabeled films are shown but not scored.

Usage:
    ollama pull qwen2.5:7b
    python scripts/compare_llms.py                      # llama3.2 vs qwen2.5:7b
    python scripts/compare_llms.py llama3.2 qwen2.5:7b
"""

import logging
import sys
import time

from langchain_ollama import ChatOllama

from moviemood.config import get_settings
from moviemood.core import recommender
from moviemood.core.catalog import get_catalog
from moviemood.core.vectorstore import get_vectorstore

WINDOW = 8  # recommend(k=4) judges the top 2*k

# query -> (should fit, should NOT fit); titles must match the catalog exactly
LABELS = {
    "feel-good, no sad ending": (
        {"Happy New Year", "Puss in Boots: The Last Wish"},
        {"It Ends with Us", "Smile 2", "Manchester by the Sea", "Collateral Beauty", "No Time to Die"},
    ),
    "kuch halka sa, rona nahi chahiye": (
        {"Rab Ne Bana Di Jodi", "Dilwale Dulhania Le Jayenge", "Koi... Mil Gaya", "Hichki"},
        {"Kal Ho Naa Ho", "Kabhi Khushi Kabhie Gham", "Rang De Basanti"},
    ),
    "a good cry": ({"A Star Is Born"}, {"Red One", "A Quiet Place", "Send Help"}),
    "dark scary horror": (
        {"A Nightmare on Elm Street", "Halloween", "Insidious", "Night of the Living Dead"},
        {"Scary Movie"},
    ),
    "funny animated movie for kids": ({"Up", "Toy Story 3", "Toy Story", "Toy Story 4", "The Lorax"}, set()),
    "mind-bending sci-fi": ({"Eternal Sunshine of the Spotless Mind", "The Matrix", "Interstellar"}, set()),
    "heist movie with a twist": ({"Ocean's Eleven", "Now You See Me", "Happy New Year"}, {"The Hangover"}),
    "Shah Rukh Khan": (
        {"My Name Is Khan", "Rab Ne Bana Di Jodi", "Dunki", "Chennai Express", "Ra.One", "Veer-Zaara",
         "Jab Tak Hai Jaan", "Jawan"},
        set(),
    ),
    "Brad Pitt": (
        {"Fury", "Meet Joe Black", "Bullet Train", "World War Z", "Fight Club", "F1",
         "Inglourious Basterds", "Babylon"},
        set(),
    ),
    "cozy and light": ({"Lilo & Stitch", "Up"}, {"Companion"}),
}


def judge_with(model: str):
    settings = get_settings()
    llm = ChatOllama(
        model=model,
        base_url=settings.ollama_base_url,
        temperature=0,
        format=recommender._Judgement.model_json_schema(),
        client_kwargs={"timeout": recommender.LLM_TIMEOUT},
    )
    recommender._get_llm = lambda: llm  # _llm_verdicts calls _get_llm()
    return recommender._llm_verdicts


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    models = sys.argv[1:] or ["llama3.2", "qwen2.5:7b"]
    catalog = get_catalog()
    windows = {}
    for query in LABELS:
        hits = get_vectorstore().similarity_search_with_score(query, k=WINDOW)
        windows[query] = [(catalog[d.metadata["tmdb_id"]], dist) for d, dist in hits if d.metadata["tmdb_id"] in catalog]

    totals = {}
    for model in models:
        judge = judge_with(model)
        judge("warm up", windows["Brad Pitt"][:1])  # load the model before timing
        right = wrong = unjudged = 0
        seconds = 0.0
        print(f"\n===== {model} =====")
        for query, window in windows.items():
            fit, misfit = LABELS[query]
            start = time.time()
            verdicts = judge(query, window)
            seconds += time.time() - start
            print(f'\n"{query}"')
            for i, (movie, _) in enumerate(window):
                if i not in verdicts:
                    unjudged += 1
                    print(f"   ??  {movie.title}")
                    continue
                fits, why = verdicts[i]
                expected = True if movie.title in fit else False if movie.title in misfit else None
                mark = "  " if expected is None else ("ok" if fits == expected else "XX")
                if expected is not None:
                    right += fits == expected
                    wrong += fits != expected
                print(f"   {mark}  {'fit ' if fits else 'MISS'}  {movie.title}: {why[:90]}")
        totals[model] = (right, wrong, unjudged, seconds)

    print("\n===== summary (labeled verdicts only) =====")
    for model, (right, wrong, unjudged, seconds) in totals.items():
        print(f"{model:<14} correct {right}/{right + wrong}  wrong {wrong}  unjudged {unjudged}  "
              f"judge time {seconds:.0f}s total, {seconds / len(LABELS):.1f}s per query")


if __name__ == "__main__":
    main()
