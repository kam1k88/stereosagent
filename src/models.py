from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class Evidence:
    value: Any
    filename: str
    paragraph_number: int
    quote: str
    document_id: str

    def to_dict(self) -> dict[str, Any]:
        value = self.value.isoformat() if isinstance(self.value, date) else self.value
        return {
            "value": value,
            "filename": self.filename,
            "paragraph_number": self.paragraph_number,
            "quote": self.quote,
            "document_id": self.document_id,
            "anchor": f"doc-{self.document_id}-p-{self.paragraph_number}",
        }


@dataclass(slots=True)
class ParsedDocument:
    document_id: str
    filename: str
    content_hash: str
    text: str
    paragraphs: list[str]
    document_type: str
    contract_number: str | None = None
    supplier: str | None = None
    supplier_evidence: Evidence | None = None
    signature_date: date | None = None
    effective_date: date | None = None
    signed_by_both: bool | None = None
    fields: dict[str, Evidence] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProposedChange:
    contract_number: str
    filename: str
    document_type: str
    field_name: str
    proposed_value: Any
    evidence: Evidence


@dataclass(slots=True)
class ResolvedField:
    value: Any = None
    evidence: Evidence | None = None
    status: str = "not_found"
    changed_by: str | None = None
    change_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = self.value.isoformat() if isinstance(self.value, date) else self.value
        return {
            "value": value,
            "status": self.status,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "changed_by": self.changed_by,
            "change_count": self.change_count,
            "history": self.history,
            "issues": self.issues,
        }


@dataclass(slots=True)
class ContractSnapshot:
    contract_number: str
    supplier: str | None
    supplier_evidence: Evidence | None
    as_of_date: date
    amount: ResolvedField
    delivery_date: ResolvedField
    related_documents: list[dict[str, Any]]
    proposed_changes: list[ProposedChange]
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_number": self.contract_number,
            "supplier": self.supplier,
            "supplier_evidence": self.supplier_evidence.to_dict() if self.supplier_evidence else None,
            "as_of_date": self.as_of_date.isoformat(),
            "amount": self.amount.to_dict(),
            "delivery_date": self.delivery_date.to_dict(),
            "related_documents": self.related_documents,
            "proposed_changes": [
                {
                    "filename": item.filename,
                    "document_type": item.document_type,
                    "field_name": item.field_name,
                    "proposed_value": item.proposed_value.isoformat()
                    if isinstance(item.proposed_value, date) else item.proposed_value,
                    "evidence": item.evidence.to_dict(),
                }
                for item in self.proposed_changes
            ],
            "issues": self.issues,
        }
