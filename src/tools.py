from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .engine import ContractEngine


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Дата должна быть в формате YYYY-MM-DD.") from exc


def _rubles(kopecks: int | None) -> str | None:
    if kopecks is None:
        return None
    whole, cents = divmod(kopecks, 100)
    return f"{whole:,}".replace(",", " ") + (f",{cents:02d}" if cents else "") + " RUB"


def _decorate_amount_field(field: dict[str, Any]) -> dict[str, Any]:
    kop = field.pop("value", None)
    field["value_kopecks"] = kop
    field["value_rubles"] = _rubles(kop) if isinstance(kop, int) else None
    if field.get("evidence"):
        evidence = field["evidence"]
        evidence["value_kopecks"] = evidence.pop("value", None)
        evidence["value_rubles"] = _rubles(evidence["value_kopecks"])
    for item in field.get("history", []):
        if isinstance(item.get("value"), int):
            kop_value = item.pop("value")
            item["value_kopecks"] = kop_value
            item["value_rubles"] = _rubles(kop_value)
    return field


def build_tools(engine: ContractEngine, as_of_date: date) -> tuple[list[dict], dict[str, Callable]]:
    """Return Ollama-compatible tool schemas and a strict allowlisted registry."""

    def search_contracts(query: str = "") -> dict[str, Any]:
        return {"documents": engine.find_documents(query)}

    def get_contract_snapshot(contract_number: str, as_of: str | None = None) -> dict[str, Any]:
        data = engine.snapshot(contract_number, _parse_date(as_of, as_of_date)).to_dict()
        data["amount"] = _decorate_amount_field(data["amount"])
        for change in data.get("proposed_changes", []):
            if change["field_name"] == "amount":
                kop = change.pop("proposed_value")
                change["proposed_value_kopecks"] = kop
                change["proposed_value_rubles"] = _rubles(kop)
                evidence = change.get("evidence") or {}
                evidence["value_kopecks"] = evidence.pop("value", None)
                evidence["value_rubles"] = _rubles(evidence["value_kopecks"])
        return data

    def analyze_contract_changes(
        contract_number: str | None = None,
        field_name: str | None = None,
        min_changes: int = 1,
    ) -> dict[str, Any]:
        rows = engine.proposed_changes(contract_number, as_of_date)
        if field_name:
            rows = [row for row in rows if row["field"] == field_name]
        for row in rows:
            if row["field"] == "amount":
                kop = row.pop("proposed_value")
                row["proposed_value_kopecks"] = kop
                row["proposed_value_rubles"] = _rubles(kop)
        counts = []
        for number in engine.contract_numbers():
            if contract_number and number != contract_number.upper():
                continue
            snap = engine.snapshot(number, as_of_date)
            for field, resolved in (("amount", snap.amount), ("delivery_date", snap.delivery_date)):
                if not field_name or field_name == field:
                    counts.append({
                        "contract_number": number,
                        "field": field,
                        "applied_change_count": resolved.change_count,
                    })
        counts = [item for item in counts if item["applied_change_count"] >= min_changes]
        return {"proposed_changes": rows, "applied_change_counts": counts}

    def find_early_deliveries(cutoff_date: str = "2026-04-01", as_of: str | None = None) -> dict[str, Any]:
        cutoff = _parse_date(cutoff_date, date(2026, 4, 1))
        review_date = _parse_date(as_of, as_of_date)
        matches = []
        for snapshot in engine.snapshots(review_date):
            delivery = snapshot.delivery_date.value
            if delivery is None or delivery >= cutoff:
                continue
            proposals = [
                {
                    "filename": item.filename,
                    "proposed_value": item.proposed_value.isoformat()
                    if hasattr(item.proposed_value, "isoformat") else item.proposed_value,
                    "paragraph": item.evidence.paragraph_number,
                    "quote": item.evidence.quote,
                }
                for item in snapshot.proposed_changes
                if item.field_name == "delivery_date"
            ]
            matches.append({
                "contract_number": snapshot.contract_number,
                "supplier": snapshot.supplier,
                "delivery_date": delivery.isoformat(),
                "delivery_evidence": snapshot.delivery_date.evidence.to_dict()
                if snapshot.delivery_date.evidence else None,
                "unapproved_delivery_changes": proposals,
            })
        return {"cutoff_date": cutoff.isoformat(), "contracts": matches}

    def trace_field(contract_number: str, field_name: str, as_of: str | None = None) -> dict[str, Any]:
        result = engine.trace_field(contract_number, field_name, _parse_date(as_of, as_of_date))
        if field_name == "amount" and isinstance(result.get("result"), dict):
            result["result"] = _decorate_amount_field(result["result"])
        return result

    def get_source_evidence(document_id: str, paragraph_number: int) -> dict[str, Any]:
        doc = engine.get_document(document_id)
        if not doc:
            return {"status": "not_found", "message": "Документ не найден в текущей сессии."}
        if paragraph_number < 1 or paragraph_number > len(doc.paragraphs):
            return {"status": "not_found", "message": "Абзац не найден."}
        return {
            "status": "ok",
            "document_id": doc.document_id,
            "filename": doc.filename,
            "paragraph_number": paragraph_number,
            "quote": doc.paragraphs[paragraph_number - 1],
        }

    schemas = [
        {"type": "function", "function": {
            "name": "search_contracts",
            "description": "Найти договоры и связанные документы по номеру, поставщику или тексту. Используй для обнаружения документов, а не для вывода действующих условий.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Номер договора, поставщик или поисковая фраза; пустая строка означает все документы."}
            }, "required": ["query"]},
        }},
        {"type": "function", "function": {
            "name": "get_contract_snapshot",
            "description": "Получить действующие сумму и срок поставки договора на конкретную дату. Сумма возвращается как value_rubles и value_kopecks. Правила применяет детерминированный resolver. Не переопределяй его результат.",
            "parameters": {"type": "object", "properties": {
                "contract_number": {"type": "string"},
                "as_of": {"type": "string", "description": "Дата YYYY-MM-DD; по умолчанию дата проверки интерфейса."}
            }, "required": ["contract_number"]},
        }},
        {"type": "function", "function": {
            "name": "analyze_contract_changes",
            "description": "Найти проекты и письма с предложенными изменениями, а также посчитать применённые изменения по полям. Суммы указаны в RUB и копейках.",
            "parameters": {"type": "object", "properties": {
                "contract_number": {"type": "string"},
                "field_name": {"type": "string", "enum": ["amount", "delivery_date"]},
                "min_changes": {"type": "integer", "minimum": 0}
            }, "required": []},
        }},
        {"type": "function", "function": {
            "name": "find_early_deliveries",
            "description": "Найти договоры с действующим сроком поставки раньше заданной даты и показать несогласованные предложения переноса.",
            "parameters": {"type": "object", "properties": {
                "cutoff_date": {"type": "string", "description": "Граница YYYY-MM-DD; по умолчанию 2026-04-01."},
                "as_of": {"type": "string", "description": "Дата проверки YYYY-MM-DD."}
            }, "required": []},
        }},
        {"type": "function", "function": {
            "name": "trace_field",
            "description": "Показать происхождение суммы или срока: базовое значение, применённые изменения, доказательства и причины проверки.",
            "parameters": {"type": "object", "properties": {
                "contract_number": {"type": "string"},
                "field_name": {"type": "string", "enum": ["amount", "delivery_date"]},
                "as_of": {"type": "string", "description": "Дата YYYY-MM-DD."}
            }, "required": ["contract_number", "field_name"]},
        }},
        {"type": "function", "function": {
            "name": "get_source_evidence",
            "description": "Получить точный текст абзаца из загруженного документа по document_id и номеру абзаца.",
            "parameters": {"type": "object", "properties": {
                "document_id": {"type": "string"},
                "paragraph_number": {"type": "integer", "minimum": 1}
            }, "required": ["document_id", "paragraph_number"]},
        }},
    ]
    registry = {
        "search_contracts": search_contracts,
        "get_contract_snapshot": get_contract_snapshot,
        "analyze_contract_changes": analyze_contract_changes,
        "find_early_deliveries": find_early_deliveries,
        "trace_field": trace_field,
        "get_source_evidence": get_source_evidence,
    }
    return schemas, registry
