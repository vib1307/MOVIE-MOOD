from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
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

    # Paths
    data_dir: Path = PROJECT_ROOT / "data"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    collection_name: str = "movies"

    # API
    # Browser origins allowed to call the API cross-origin, e.g. in .env:
    # CORS_ORIGINS=["http://localhost:4200"]. Empty = same-origin only. Never "*".
    cors_origins: list[str] = []

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
