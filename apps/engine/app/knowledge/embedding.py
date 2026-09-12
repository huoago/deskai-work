from __future__ import annotations

import hashlib
import math
import re
import shutil
import unicodedata
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
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
BGE_M3_MODEL = "bge-m3-onnx-int8-v1"
BGE_M3_DIMENSION = 1024
BGE_M3_MODEL_URL = "https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx"
BGE_M3_TOKENIZER_URL = "https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json"
BGE_M3_MODEL_SHA256 = "0826f8c1ab9edf1801db86c61919d4d108e8bfc0b809ec823ad366882ff0b77d"
BGE_M3_TOKENIZER_SHA256 = "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
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


@dataclass(frozen=True, slots=True)
class LocalModelStatus:
    model: str
    installed: bool
    model_path: str
    tokenizer_path: str
    model_sha256: str
    tokenizer_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "installed": self.installed,
            "model_path": self.model_path,
            "tokenizer_path": self.tokenizer_path,
            "model_sha256": self.model_sha256,
            "tokenizer_sha256": self.tokenizer_sha256,
            "download_required": not self.installed,
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
    """Deterministic compatibility embedding used by the offline fallback provider."""
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class BgeM3ModelManager:
    def __init__(self, data_dir: Path) -> None:
        self.model_dir = data_dir / "models" / "bge-m3"
        self.model_path = self.model_dir / "model_quantized.onnx"
        self.tokenizer_path = self.model_dir / "tokenizer.json"

    def status(self) -> LocalModelStatus:
        installed = (
            self.model_path.is_file()
            and self.tokenizer_path.is_file()
            and _sha256(self.model_path) == BGE_M3_MODEL_SHA256
            and _sha256(self.tokenizer_path) == BGE_M3_TOKENIZER_SHA256
        )
        return LocalModelStatus(
            model=BGE_M3_MODEL,
            installed=installed,
            model_path=str(self.model_path),
            tokenizer_path=str(self.tokenizer_path),
            model_sha256=BGE_M3_MODEL_SHA256,
            tokenizer_sha256=BGE_M3_TOKENIZER_SHA256,
        )

    def install(self) -> LocalModelStatus:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._download(BGE_M3_TOKENIZER_URL, self.tokenizer_path, BGE_M3_TOKENIZER_SHA256)
        self._download(BGE_M3_MODEL_URL, self.model_path, BGE_M3_MODEL_SHA256)
        status = self.status()
        if not status.installed:
            raise EmbeddingError("BGE-M3 local model verification failed after download")
        return status

    def remove(self) -> LocalModelStatus:
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir)
        return self.status()

    @staticmethod
    def _download(url: str, destination: Path, expected_sha256: str) -> None:
        if destination.is_file() and _sha256(destination) == expected_sha256:
            return
        part = destination.with_suffix(destination.suffix + ".part")
        part.unlink(missing_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "DeskAI-Work/0.27"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        except Exception as exc:
            part.unlink(missing_ok=True)
            raise EmbeddingError(f"BGE-M3 download failed: {exc}") from exc
        actual = _sha256(part)
        if actual != expected_sha256:
            part.unlink(missing_ok=True)
            raise EmbeddingError(
                f"BGE-M3 asset checksum mismatch: expected {expected_sha256}, got {actual}"
            )
        part.replace(destination)


class LocalHashEmbeddingProvider:
    name = "local_hash"
    model = EMBEDDING_PROVIDER
    dimension = EMBEDDING_DIMENSION

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [embed_text(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return embed_text(text)


class LocalBgeM3EmbeddingProvider:
    name = "local_bge_m3"
    model = BGE_M3_MODEL
    dimension = BGE_M3_DIMENSION

    def __init__(self, manager: BgeM3ModelManager) -> None:
        status = manager.status()
        if not status.installed:
            raise EmbeddingError("BGE-M3 is not installed. Download the local model before selecting it.")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingError("Local BGE-M3 runtime is unavailable") from exc
        self._np = np
        self._tokenizer = Tokenizer.from_file(str(manager.tokenizer_path))
        self._tokenizer.enable_truncation(max_length=8192)
        self._session = ort.InferenceSession(
            str(manager.model_path),
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {item.name for item in self._session.get_inputs()}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        encodings = self._tokenizer.encode_batch(texts)
        max_length = max(len(item.ids) for item in encodings)
        input_ids = []
        attention_mask = []
        for item in encodings:
            padding = max_length - len(item.ids)
            input_ids.append(item.ids + [1] * padding)
            attention_mask.append(item.attention_mask + [0] * padding)
        feeds = {
            "input_ids": self._np.asarray(input_ids, dtype=self._np.int64),
            "attention_mask": self._np.asarray(attention_mask, dtype=self._np.int64),
        }
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = self._np.zeros_like(feeds["input_ids"])
        try:
            outputs = self._session.run(None, feeds)
        except Exception as exc:
            raise EmbeddingError(f"Local BGE-M3 inference failed: {exc}") from exc
        hidden = outputs[0]
        pooled = hidden[:, 0, :]
        norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = self._np.where(norms == 0, 1.0, norms)
        normalized = pooled / norms
        vectors = normalized.astype(self._np.float32).tolist()
        if any(len(vector) != self.dimension for vector in vectors):
            raise EmbeddingError("Local BGE-M3 output dimension mismatch")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


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
        return self.embed_documents([text])[0]


class EmbeddingService:
    """Selects the configured provider without exposing credentials to callers."""

    def __init__(
        self,
        database: Database | None = None,
        secret_store: SecretStore | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.database = database
        self.secret_store = secret_store
        self.model_manager = BgeM3ModelManager(data_dir) if data_dir is not None else None

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
        if provider == "local_bge_m3":
            return EmbeddingDescriptor("local_bge_m3", BGE_M3_MODEL, BGE_M3_DIMENSION, True)
        return EmbeddingDescriptor("local_hash", EMBEDDING_PROVIDER, EMBEDDING_DIMENSION, False)

    def local_model_status(self) -> dict[str, object]:
        if self.model_manager is None:
            raise EmbeddingError("Local model storage is unavailable")
        return self.model_manager.status().as_dict()

    def install_local_model(self) -> dict[str, object]:
        if self.model_manager is None:
            raise EmbeddingError("Local model storage is unavailable")
        return self.model_manager.install().as_dict()

    def remove_local_model(self) -> dict[str, object]:
        if self.model_manager is None:
            raise EmbeddingError("Local model storage is unavailable")
        return self.model_manager.remove().as_dict()

    def provider(self) -> EmbeddingProvider:
        descriptor = self.descriptor()
        if descriptor.provider == "local_hash":
            return LocalHashEmbeddingProvider()
        if descriptor.provider == "local_bge_m3":
            if self.model_manager is None:
                raise EmbeddingError("Local BGE-M3 storage is unavailable")
            return LocalBgeM3EmbeddingProvider(self.model_manager)
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
