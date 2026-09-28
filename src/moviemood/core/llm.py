"""One place that builds the LLM, for Ollama or OpenAI (settings.llm_provider). See D-033.

structured_llm(schema) returns a runnable: messages in, a validated `schema` object out.
Callers (recommender, lazy_ingest) don't know or care which provider answered:

    judge = structured_llm(_Judgement, timeout=120)
    judge.invoke([("system", "..."), ("human", "...")])  # -> _Judgement(verdicts=[...])

Any failure (timeout, API error, bad JSON) raises; the callers already catch it and fall
back (retrieval order, or no lazy ingest).
"""

from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from moviemood.config import get_settings


def structured_llm(schema: type[BaseModel], timeout: int) -> Runnable:
    settings = get_settings()
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI  # only needed (and installed) for openai

        llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,  # same query -> same answer (D-017)
            timeout=timeout,
            max_retries=1,
        )
        # Function calling, not strict json_schema: strict mode rejects our optional
        # fields and minItems. Pydantic still validates the answer.
        return llm.with_structured_output(schema, method="function_calling")

    from langchain_ollama import ChatOllama

    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        format=schema.model_json_schema(),  # Ollama constrains output to this JSON shape
        client_kwargs={"timeout": timeout},
    )
    return llm | RunnableLambda(lambda msg: schema.model_validate_json(msg.content))
