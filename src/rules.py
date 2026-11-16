from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_RULES: dict[str, Any] = {
    "rule_set_id": "procurement_policy",
    "version": 1,
    "amendment": {
        "require_both_signatures": True,
        "require_signature_date": True,
        "require_effective_date": True,
        "signature_date_lte_as_of": True,
        "effective_date_lte_as_of": True,
    },
    "non_binding_types": ["amendment_draft", "supplier_email"],
    "conflict_strategy": "require_review",
}


def load_rules(path: Path | None = None) -> dict[str, Any]:
    if path is None or not path.exists():
        return DEFAULT_RULES.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("amendment"), dict):
        raise ValueError("Некорректная конфигурация rules.json: требуется объект amendment.")
    if not isinstance(data.get("rule_set_id"), str) or not data["rule_set_id"].strip():
        raise ValueError("rule_set_id должен быть непустой строкой.")
    if not isinstance(data.get("version"), int) or isinstance(data["version"], bool) or data["version"] < 1:
        raise ValueError("version должен быть положительным целым числом.")

    required = {
        "require_both_signatures", "require_signature_date",
        "require_effective_date", "signature_date_lte_as_of",
        "effective_date_lte_as_of",
    }
    missing = required - set(data["amendment"])
    if missing:
        raise ValueError(f"В конфигурации отсутствуют правила: {sorted(missing)}")
    for key in required:
        if not isinstance(data["amendment"][key], bool):
            raise ValueError(f"{key} должен иметь логическое значение.")
    if data.get("conflict_strategy", "require_review") != "require_review":
        raise ValueError("Пока поддерживается только conflict_strategy=require_review.")
    if not isinstance(data.get("non_binding_types", []), list):
        raise ValueError("non_binding_types должен быть списком.")
    return data
