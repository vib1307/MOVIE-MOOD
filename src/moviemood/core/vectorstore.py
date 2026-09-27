"""Chroma vector store with nomic embeddings. See D-016.

Chroma finds movies; data/movies.json holds their full details.
"""

import logging
from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from moviemood.config import get_settings
from moviemood.core.models import Movie
from moviemood.core.semantic_text import build_document_text, to_metadata

log = logging.getLogger(__name__)

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class NomicEmbeddings(OllamaEmbeddings):
    """OllamaEmbeddings that adds nomic's task prefixes automatically."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([DOC_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        # The parent's embed_query calls self.embed_documents(), which would hit our
        # override and double-prefix ("search_document: search_query: ..."). So call
        # the parent's embed_documents directly.
        return super().embed_documents([QUERY_PREFIX + text])[0]


def _new_store() -> Chroma:
    settings = get_settings()
    return Chroma(
        collection_name=settings.collection_name,
        embedding_function=NomicEmbeddings(
            model=settings.embed_model, base_url=settings.ollama_base_url
        ),
        persist_directory=str(settings.chroma_dir),
        # Only takes effect when the collection is created; build_index recreates it.
        collection_configuration={"hnsw": {"space": "cosine"}},
    )


@lru_cache
def get_vectorstore() -> Chroma:
    """One shared store per process (the API and UI reuse it)."""
    return _new_store()


def build_index(movies: list[Movie]) -> int:
    """Full rebuild: drop the collection and embed every movie again.

    Keeps the index exactly in sync with movies.json (no stale movies), and picks up
    blob-format or metric changes. ~1 min for 500 movies.
    """
    store = get_vectorstore()
    store.delete_collection()
    get_vectorstore.cache_clear()  # the old object points at the deleted collection
    store = get_vectorstore()

    docs = [
        Document(page_content=build_document_text(m), metadata=to_metadata(m)) for m in movies
    ]
    ids = [str(m.tmdb_id) for m in movies]
    store.add_documents(docs, ids=ids)
    count = store._collection.count()
    log.info("indexed %d movies into %s", count, get_settings().chroma_dir)
    return count
