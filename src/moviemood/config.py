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
    llm_model: str = "llama3.2"

    # Paths
    data_dir: Path = PROJECT_ROOT / "data"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    collection_name: str = "movies"

    @property
    def movies_json(self) -> Path:
        return self.data_dir / "movies.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
