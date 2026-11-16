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


def build_tools(engine: ContractEngine, as_of_date: date) -> tuple[list[dict], dict[str, Callable]]:
    """Return Ollama-compatible tool schemas and a strict allowlisted registry."""

    def search_contracts(query: str = "") -> dict[str, Any]:
        return {"documents": engine.find_documents(query)}

    def get_contract_snapshot(contract_number: str, as_of: str | None = None) -> dict[str, Any]:
        return engine.snapshot(contract_number, _parse_date(as_of, as_of_date)).to_dict()

    def analyze_contract_changes(
        contract_number: str | None = None,
        field_name: str | None = None,
        min_changes: int = 1,
    ) -> dict[str, Any]:
        rows = engine.proposed_changes(contract_number, as_of_date)
        if field_name:
            rows = [row for row in rows if row["field"] == field_name]
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

    def trace_field(contract_number: str, field_name: str, as_of: str | None = None) -> dict[str, Any]:
        return engine.trace_field(contract_number, field_name, _parse_date(as_of, as_of_date))

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
            "description": "Получить действующие сумму и срок поставки договора на конкретную дату. Правила применяет детерминированный resolver. Не переопределяй его результат.",
            "parameters": {"type": "object", "properties": {
                "contract_number": {"type": "string"},
                "as_of": {"type": "string", "description": "Дата YYYY-MM-DD; по умолчанию дата проверки интерфейса."}
            }, "required": ["contract_number"]},
        }},
        {"type": "function", "function": {
            "name": "analyze_contract_changes",
            "description": "Найти проекты и письма с предложенными изменениями, а также посчитать применённые изменения по полям.",
            "parameters": {"type": "object", "properties": {
                "contract_number": {"type": "string"},
                "field_name": {"type": "string", "enum": ["amount", "delivery_date"]},
                "min_changes": {"type": "integer", "minimum": 0}
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
        "trace_field": trace_field,
        "get_source_evidence": get_source_evidence,
    }
    return schemas, registry
