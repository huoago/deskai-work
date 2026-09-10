from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter

EMBEDDING_DIMENSION = 384
EMBEDDING_PROVIDER = "local-hash-384-v1"
WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ_./:+-]+")
CJK_RE = re.compile(r"[\u3400-\u9fff]+")


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
