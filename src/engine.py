from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from .extractor import extract_document
from .models import ParsedDocument
from .resolver import resolve_contract
from .rules import load_rules


class ContractEngine:
    def __init__(self, documents: list[ParsedDocument], rules: dict | None = None):
        self.documents = documents
        self.rules = rules or load_rules()

    @classmethod
    def from_sources(cls, sources: Iterable[tuple[str, bytes]], rules_path: Path | None = None):
        documents = []
        seen: set[str] = set()
        for filename, raw in sources:
            doc = extract_document(filename, raw)
            if doc.content_hash in seen:
                continue
            seen.add(doc.content_hash)
            documents.append(doc)
        return cls(documents, load_rules(rules_path))

    def contract_numbers(self) -> list[str]:
        return sorted({
            d.contract_number for d in self.documents
            if d.contract_number and d.document_type == "contract"
        })

    def snapshots(self, as_of: date) -> list:
        return [resolve_contract(number, self.documents, as_of, self.rules)
                for number in self.contract_numbers()]

    def snapshot(self, contract_number: str, as_of: date):
        return resolve_contract(contract_number.upper(), self.documents, as_of, self.rules)

    def find_documents(self, query: str = "") -> list[dict]:
        q = query.casefold().strip()
        results = []
        for doc in self.documents:
            haystack = " ".join([doc.filename, doc.contract_number or "", doc.supplier or "", doc.text]).casefold()
            if not q or q in haystack:
                results.append({
                    "document_id": doc.document_id,
                    "filename": doc.filename,
                    "type": doc.document_type,
                    "contract_number": doc.contract_number,
                    "supplier": doc.supplier,
                    "issues": doc.issues,
                })
        return results

    def get_document(self, document_id: str):
        return next((d for d in self.documents if d.document_id == document_id), None)

    def proposed_changes(self, contract_number: str | None, as_of: date) -> list[dict]:
        snapshots = self.snapshots(as_of)
        if contract_number:
            snapshots = [s for s in snapshots if s.contract_number == contract_number.upper()]
        rows = []
        for snapshot in snapshots:
            for item in snapshot.proposed_changes:
                value = item.proposed_value.isoformat() if hasattr(item.proposed_value, "isoformat") else item.proposed_value
                rows.append({
                    "contract_number": item.contract_number,
                    "filename": item.filename,
                    "document_type": item.document_type,
                    "field": item.field_name,
                    "proposed_value": value,
                    "evidence": item.evidence.to_dict(),
                })
        return rows

    def trace_field(self, contract_number: str, field_name: str, as_of: date) -> dict:
        snapshot = self.snapshot(contract_number, as_of)
        if field_name not in ("amount", "delivery_date"):
            return {"status": "unsupported_field", "message": "Поддерживаются amount и delivery_date."}
        field = snapshot.amount if field_name == "amount" else snapshot.delivery_date
        return {
            "contract_number": contract_number.upper(),
            "field": field_name,
            "result": field.to_dict(),
            "contract_issues": snapshot.issues,
        }
