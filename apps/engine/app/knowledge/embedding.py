from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI, OpenAIError

from app.database.models import Setting
from app.database.session import Database
from app.security.secrets import SecretStore

EMBEDDING_DIMENSION = 384
EMBEDDING_PROVIDER = "local-hash-384-v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}
WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ_./:+-]+")
CJK_RE = re.compile(r"[\u3400-\u9fff]+")


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class EmbeddingDescriptor:
    provider: str
    model: str
    dimension: int
    semantic: bool

    @property
    def signature(self) -> str:
        if self.provider == "local_hash":
            return EMBEDDING_PROVIDER
        return f"{self.provider}:{self.model}:{self.dimension}"

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "semantic": self.semantic,
            "signature": self.signature,
        }


def _features(text: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    features: Counter[str] = Counter()

    for word in WORD_RE.findall(normalized):
        features[f"w:{word}"] += 2

    compact = "".join(ch for ch in normalized if not ch.isspace())
    if len(compact) < 3:
        for ch in compact:
            features[f"c1:{ch}"] += 1
    else:
        for size in (3, 4):
            for index in range(max(0, len(compact) - size + 1)):
                features[f"c{size}:{compact[index:index + size]}"] += 1

    for segment in CJK_RE.findall(normalized):
        if len(segment) < 3:
            for ch in segment:
                features[f"zh:{ch}"] += 2
        else:
            for index in range(len(segment) - 2):
                features[f"zh3:{segment[index:index + 3]}"] += 3
    return features


def embed_text(text: str, dimension: int = EMBEDDING_DIMENSION) -> list[float]:
    """Deterministic compatibility embedding used by the offline fallback provider.

    It remains available so existing Phase 4 indexes/tests continue to work, but
    it is not presented as a semantic embedding model.
    """
    vector = [0.0] * dimension
    for feature, count in _features(text).items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimension
        sign = -1.0 if value & (1 << 63) else 1.0
        vector[index] += sign * (1.0 + math.log(count))

    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


class LocalHashEmbeddingProvider:
    name = "local_hash"
    model = EMBEDDING_PROVIDER
    dimension = EMBEDDING_DIMENSION

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return embed_text(text)


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        if model not in OPENAI_MODEL_DIMENSIONS:
            raise EmbeddingError(f"Unsupported OpenAI embedding model: {model}")
        self.model = model
        self.dimension = OPENAI_MODEL_DIMENSIONS[model]
        self._client = OpenAI(api_key=api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
        except OpenAIError as exc:
            raise EmbeddingError(f"OpenAI embedding request failed: {exc}") from exc
        rows = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in rows]
        if len(vectors) != len(texts):
            raise EmbeddingError("OpenAI embedding response count did not match the request")
        if any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingError("OpenAI embedding dimension did not match the configured model")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0]


class EmbeddingService:
    """Selects the configured provider without exposing credentials to callers."""

    def __init__(self, database: Database | None = None, secret_store: SecretStore | None = None) -> None:
        self.database = database
        self.secret_store = secret_store

    def _setting(self, key: str, default: object) -> object:
        if self.database is None:
            return default
        with self.database.session() as session:
            item = session.get(Setting, key)
            return item.value_json if item is not None else default

    def descriptor(self) -> EmbeddingDescriptor:
        provider = str(self._setting("embedding_provider", "local_hash"))
        if provider == "openai":
            model = str(self._setting("embedding_model", DEFAULT_OPENAI_EMBEDDING_MODEL))
            dimension = OPENAI_MODEL_DIMENSIONS.get(model)
            if dimension is None:
                raise EmbeddingError(f"Unsupported OpenAI embedding model: {model}")
            return EmbeddingDescriptor("openai", model, dimension, True)
        return EmbeddingDescriptor("local_hash", EMBEDDING_PROVIDER, EMBEDDING_DIMENSION, False)

    def provider(self) -> EmbeddingProvider:
        descriptor = self.descriptor()
        if descriptor.provider == "local_hash":
            return LocalHashEmbeddingProvider()

        privacy_mode = str(self._setting("privacy_mode", "hybrid"))
        if privacy_mode == "local":
            raise EmbeddingError("Cloud embeddings are disabled in Local Only mode")
        if self.secret_store is None:
            raise EmbeddingError("OpenAI credential store is unavailable")
        api_key = self.secret_store.get_openai_api_key()
        if not api_key:
            raise EmbeddingError("OpenAI API key is not configured")
        return OpenAIEmbeddingProvider(api_key=api_key, model=descriptor.model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.provider().embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.provider().embed_query(text)
