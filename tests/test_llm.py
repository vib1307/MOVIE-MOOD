"""core/llm.py picks the provider from settings (D-033). No network: the chat model
classes are replaced with fakes."""

import langchain_ollama
import langchain_openai
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import BaseModel, ValidationError

from moviemood.config import Settings
from moviemood.core import llm


class Answer(BaseModel):
    fits: bool
    why: str


def settings(**overrides) -> Settings:
    # _env_file=None: the developer's .env (maybe LLM_PROVIDER=openai) must not leak in
    return Settings(_env_file=None, tmdb_api_key="t", omdb_api_key="o", **overrides)


def test_ollama_answer_is_parsed_into_the_schema(monkeypatch):
    built = {}

    def fake_ollama(**kwargs):
        built.update(kwargs)
        return FakeListChatModel(responses=['{"fits": true, "why": "Warm and funny."}'])

    monkeypatch.setattr(llm, "get_settings", lambda: settings())
    monkeypatch.setattr(langchain_ollama, "ChatOllama", fake_ollama)

    answer = llm.structured_llm(Answer, timeout=5).invoke([("human", "cozy")])

    assert answer == Answer(fits=True, why="Warm and funny.")
    assert built["model"] == "qwen2.5:7b" and built["temperature"] == 0
    assert built["format"] == Answer.model_json_schema()  # Ollama is constrained to the shape


def test_ollama_bad_json_raises(monkeypatch):
    # The callers catch this and fall back (retrieval order / no lazy ingest)
    monkeypatch.setattr(llm, "get_settings", lambda: settings())
    monkeypatch.setattr(langchain_ollama, "ChatOllama",
                        lambda **kw: FakeListChatModel(responses=["not json"]))
    with pytest.raises(ValidationError):
        llm.structured_llm(Answer, timeout=5).invoke([("human", "cozy")])


def test_openai_uses_key_model_and_function_calling(monkeypatch):
    built = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            built.update(kwargs)

        def with_structured_output(self, schema, method):
            built["schema"], built["method"] = schema, method
            return "runnable"

    s = settings(llm_provider="openai", openai_api_key="sk-test", openai_model="gpt-test")
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)

    assert llm.structured_llm(Answer, timeout=7) == "runnable"
    assert built["model"] == "gpt-test"
    assert built["api_key"].get_secret_value() == "sk-test"
    assert (built["temperature"], built["timeout"]) == (0, 7)
    assert (built["schema"], built["method"]) == (Answer, "function_calling")


def test_openai_without_key_fails_at_startup():
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        settings(llm_provider="openai")
