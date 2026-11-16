from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", text).strip()


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]


def load_path(path: Path) -> tuple[str, bytes]:
    raw = path.read_bytes()
    return path.name, raw


def uploaded_to_sources(uploaded_files: Iterable) -> list[tuple[str, bytes]]:
    sources: list[tuple[str, bytes]] = []
    for item in uploaded_files:
        sources.append((item.name, item.getvalue()))
    return sources


def sample_sources(folder: Path) -> list[tuple[str, bytes]]:
    if not folder.exists():
        return []
    return [(p.name, p.read_bytes()) for p in sorted(folder.glob("*.txt"))]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
