from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from .extractor import extract_document
from .models import ParsedDocument
from .resolver import resolve_contract
from .rules import load_rules


def _party_key(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(
        value.casefold().replace("«", "").replace("»", "").replace('"', "").split()
    ).strip(" .;,:")


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

    def party_role(self, contract_number: str, party_name: str | None) -> str:
        """Resolve a party's role in this contract, never globally by organization name."""
        if not party_name:
            return "unknown"
        contracts = [
            d for d in self.documents
            if d.document_type == "contract" and d.contract_number == contract_number.upper()
        ]
        if len(contracts) != 1:
            return "unknown"
        contract = contracts[0]
        key = _party_key(party_name)
        if key and key == _party_key(contract.supplier):
            return "supplier"
        if key and key == _party_key(contract.buyer):
            return "buyer"
        return "unknown"

    def find_documents(self, query: str = "") -> list[dict]:
        q = query.casefold().strip()
        results = []
        for doc in self.documents:
            haystack = " ".join([
                doc.filename, doc.contract_number or "", doc.supplier or "",
                doc.buyer or "", doc.sender or "", doc.recipient or "", doc.text
            ]).casefold()
            if not q or q in haystack:
                results.append({
                    "document_id": doc.document_id,
                    "filename": doc.filename,
                    "type": doc.document_type,
                    "contract_number": doc.contract_number,
                    "supplier": doc.supplier,
                    "buyer": doc.buyer,
                    "sender": doc.sender,
                    "recipient": doc.recipient,
                    "issues": doc.issues,
                })
        return results

    def get_document(self, document_id: str):
        return next((d for d in self.documents if d.document_id == document_id), None)

    def proposed_changes(
        self,
        contract_number: str | None,
        as_of: date,
        field_name: str | None = None,
        party_role: str | None = None,
        status: str = "PROPOSED",
    ) -> list[dict]:
        snapshots = self.snapshots(as_of)
        if contract_number:
            snapshots = [s for s in snapshots if s.contract_number == contract_number.upper()]
        rows = []
        for snapshot in snapshots:
            for item in snapshot.proposed_changes:
                if field_name and item.field_name != field_name:
                    continue
                doc = self.get_document(item.evidence.document_id)
                initiator = doc.sender if doc else None
                recipient = doc.recipient if doc else None
                initiator_role = self.party_role(snapshot.contract_number, initiator)
                recipient_role = self.party_role(snapshot.contract_number, recipient)
                # Only explicitly non-binding documents are surfaced as proposals.
                row = {
                    "contract_number": item.contract_number,
                    "filename": item.filename,
                    "document_type": item.document_type,
                    "field": item.field_name,
                    "proposed_value": item.proposed_value.isoformat()
                    if hasattr(item.proposed_value, "isoformat") else item.proposed_value,
                    "evidence": item.evidence.to_dict(),
                    "initiator": initiator,
                    "initiator_role": initiator_role,
                    "recipient": recipient,
                    "recipient_role": recipient_role,
                    "status": status,
                }
                if party_role and initiator_role != party_role:
                    continue
                rows.append(row)
        return rows
