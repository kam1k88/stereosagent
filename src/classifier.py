from __future__ import annotations

import re

from .ingestion import normalize_text


def classify_document(text: str) -> str:
    normalized = normalize_text(text).casefold()
    if "проект дополнительного соглашения" in normalized or (
        "дополнительное соглашение" in normalized
        and ("проект" in normalized[:500] or "не подписан" in normalized[:1000])
    ):
        return "amendment_draft"
    if "письмо поставщика" in normalized or (
        "тема:" in normalized and "предлагаем перенести" in normalized
    ):
        return "supplier_email"
    if "дополнительное соглашение" in normalized:
        return "amendment_signed"
    if re.search(r"договор\s+поставки\s*(?:№|n)", normalized):
        return "contract"
    return "unknown"
