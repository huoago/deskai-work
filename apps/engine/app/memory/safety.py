from __future__ import annotations

import json
import re
from typing import Any

SECRET_VALUE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|contraseña|密码|密钥)\b", re.I),
    re.compile(r"\b(?:cvv|cvc|credit card|银行卡|信用卡|身份证|护照|passport|ssn)\b", re.I),
]

SENSITIVE_PERSONAL_PATTERNS = [
    re.compile(r"\b(?:diagnosis|diagnosed|medication|medical condition|health condition|religion|religious|political party|sexual orientation|sex life|ethnicity|race|trade union)\b", re.I),
    re.compile(r"(?:诊断|疾病|用药|病史|宗教|政治党派|政治立场|性取向|性生活|种族|民族|工会)"),
]

CARD_LIKE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _serialized(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def is_sensitive_memory(*, subject: str, predicate: str, value: Any) -> bool:
    text = f"{subject}\n{predicate}\n{_serialized(value)}"
    if any(pattern.search(text) for pattern in SECRET_VALUE_PATTERNS):
        return True
    if any(pattern.search(text) for pattern in SENSITIVE_PERSONAL_PATTERNS):
        return True
    if CARD_LIKE.search(text) and any(
        marker in text.lower()
        for marker in ("card", "银行卡", "信用卡", "cvv", "cvc")
    ):
        return True
    return False


def safe_memory_text(value: Any, *, max_chars: int = 4000) -> str:
    text = _serialized(value)
    return text[:max_chars]
