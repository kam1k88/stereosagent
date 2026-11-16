from __future__ import annotations

from .models import ParsedDocument


def normalize_party_name(value: str | None) -> str:
    """Normalize legal-entity names for exact, conservative party matching."""
    if not value:
        return ""
    return " ".join(
        value.casefold().replace("«", "").replace("»", "").replace('"', "").split()
    ).strip(" .;,:")


def resolve_party_role(
    contract_number: str,
    party_name: str | None,
    documents: list[ParsedDocument],
) -> str:
    """Resolve buyer/supplier role only within the referenced contract."""
    key = normalize_party_name(party_name)
    if not key:
        return "unknown"

    contracts = [
        doc for doc in documents
        if doc.document_type == "contract"
        and doc.contract_number == contract_number.upper()
    ]
    # Ambiguous base contracts must never produce a guessed role.
    if len(contracts) != 1:
        return "unknown"

    contract = contracts[0]
    if key == normalize_party_name(contract.supplier):
        return "supplier"
    if key == normalize_party_name(contract.buyer):
        return "buyer"
    return "unknown"
