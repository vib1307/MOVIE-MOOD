from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/moviemood/config.py -> parents[2] is the project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required: no default, so startup fails loudly if missing
    tmdb_api_key: SecretStr
    omdb_api_key: SecretStr

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    # qwen2.5:7b judged 85% of labeled cases right vs 62% for llama3.2, ~2x slower (D-026).
    # Override with LLM_MODEL=llama3.2 in .env for the faster, smaller model.
    llm_model: str = "qwen2.5:7b"

    # LLM provider (D-033). "ollama" uses llm_model above; "openai" uses openai_model and
    # needs OPENAI_API_KEY. Embeddings stay on Ollama (nomic) either way.
    llm_provider: Literal["ollama", "openai"] = "ollama"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"  # cheap + fast, and accepts temperature=0

    # Paths
    data_dir: Path = PROJECT_ROOT / "data"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    collection_name: str = "movies"

    # API
    # Browser origins allowed to call the API cross-origin, e.g. in .env:
    # CORS_ORIGINS=["http://localhost:4200"]. Empty = same-origin only. Never "*".
    cors_origins: list[str] = []

    @model_validator(mode="after")
    def _openai_needs_key(self) -> "Settings":
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai needs OPENAI_API_KEY in .env")
        return self

    @property
    def movies_json(self) -> Path:
        return self.data_dir / "movies.json"

    @property
    def added_ids_json(self) -> Path:
        """TMDB ids added at runtime by lazy ingestion; fetch_movies.py keeps them (D-024)."""
        return self.data_dir / "added_ids.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
