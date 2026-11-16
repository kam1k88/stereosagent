from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ..models import ParsedDocument


def _json_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    return json.dumps(value, ensure_ascii=False, default=str)


class PostgresRepository:
    """Persistence adapter for parsed documents and their source evidence.

    The repository deliberately stores extracted assertions separately from
    legally resolved contract conditions. The resolver remains the source of
    truth for which condition applies on a review date.
    """

    def __init__(self, connection):
        self.connection = connection

    def _party_id(self, name: str | None) -> int | None:
        if not name:
            return None
        row = self.connection.execute(
            """
            INSERT INTO parties (name)
            VALUES (%s)
            ON CONFLICT (lower(name)) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (name.strip(),),
        ).fetchone()
        return row[0]

    def _contract_id(self, contract_number: str, buyer_id: int | None,
                     supplier_id: int | None) -> int:
        row = self.connection.execute(
            """
            INSERT INTO contracts (contract_number, buyer_id, supplier_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (contract_number) DO UPDATE SET
                buyer_id = COALESCE(EXCLUDED.buyer_id, contracts.buyer_id),
                supplier_id = COALESCE(EXCLUDED.supplier_id, contracts.supplier_id)
            RETURNING id
            """,
            (contract_number, buyer_id, supplier_id),
        ).fetchone()
        return row[0]

    def _evidence_id(self, document_db_id: int, evidence) -> int:
        row = self.connection.execute(
            """
            INSERT INTO evidence (document_id, paragraph_number, quote)
            VALUES (%s, %s, %s)
            ON CONFLICT (document_id, paragraph_number, quote)
            DO UPDATE SET quote = EXCLUDED.quote
            RETURNING id
            """,
            (document_db_id, evidence.paragraph_number, evidence.quote),
        ).fetchone()
        return row[0]

    def persist_document(self, document: ParsedDocument) -> int:
        """Upsert one parsed document, its parties, extracted fields and evidence."""
        buyer_id = self._party_id(document.buyer)
        supplier_id = self._party_id(document.supplier)
        contract_id = None
        if document.contract_number:
            contract_id = self._contract_id(
                document.contract_number, buyer_id, supplier_id
            )

        metadata = {
            "signed_by_both": document.signed_by_both,
            "issues": document.issues,
        }
        row = self.connection.execute(
            """
            INSERT INTO documents (
                external_id, contract_id, filename, document_type, content_hash,
                content, sender, recipient, signature_date, effective_date, metadata
            )
            VALUES (%s, %s, %s, %s::document_type, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (content_hash) DO UPDATE SET
                contract_id = COALESCE(EXCLUDED.contract_id, documents.contract_id),
                filename = EXCLUDED.filename,
                document_type = EXCLUDED.document_type,
                content = EXCLUDED.content,
                sender = EXCLUDED.sender,
                recipient = EXCLUDED.recipient,
                signature_date = EXCLUDED.signature_date,
                effective_date = EXCLUDED.effective_date,
                metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                document.document_id, contract_id, document.filename,
                document.document_type, document.content_hash, document.text,
                document.sender, document.recipient, document.signature_date,
                document.effective_date, json.dumps(metadata, ensure_ascii=False),
            ),
        ).fetchone()
        document_db_id = row[0]

        facts = dict(document.fields)
        if document.supplier_evidence:
            facts["supplier"] = document.supplier_evidence
        if document.buyer_evidence:
            facts["buyer"] = document.buyer_evidence

        for field_name, evidence in facts.items():
            evidence_id = self._evidence_id(document_db_id, evidence)
            self.connection.execute(
                """
                INSERT INTO document_facts
                    (document_id, field_name, value_json, evidence_id)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (document_id, field_name) DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    evidence_id = EXCLUDED.evidence_id,
                    extracted_at = now()
                """,
                (
                    document_db_id, field_name, _json_value(evidence.value),
                    evidence_id,
                ),
            )

        # A positive "signed by both" assertion is recorded for both known parties.
        # False/unknown is not expanded into individual signature claims.
        if document.signed_by_both is True and document.signature_date:
            for party_id in (buyer_id, supplier_id):
                if party_id is not None:
                    self.connection.execute(
                        """
                        INSERT INTO document_signatures
                            (document_id, party_id, status, signed_at)
                        VALUES (%s, %s, 'signed', %s)
                        ON CONFLICT (document_id, party_id) DO UPDATE SET
                            status = 'signed', signed_at = EXCLUDED.signed_at
                        """,
                        (document_db_id, party_id, document.signature_date),
                    )

        return document_db_id

    def link_documents(self, source_document_id: int, target_document_id: int,
                       relation: str) -> None:
        self.connection.execute(
            """
            INSERT INTO document_relations
                (source_document_id, target_document_id, relation)
            VALUES (%s, %s, %s::relation_type)
            ON CONFLICT (source_document_id, target_document_id, relation) DO NOTHING
            """,
            (source_document_id, target_document_id, relation),
        )

    def add_document_reference(
        self,
        source_document_id: int,
        referenced_title: str,
        reference_type: str = "other",
        status: str = "not_checked",
        resolved_document_id: int | None = None,
        evidence_id: int | None = None,
    ) -> int:
        if status == "resolved" and resolved_document_id is None:
            raise ValueError("Для resolved-ссылки нужен resolved_document_id.")
        if status != "resolved" and resolved_document_id is not None:
            raise ValueError("resolved_document_id допустим только для resolved-ссылки.")
        row = self.connection.execute(
            """
            INSERT INTO document_references (
                source_document_id, referenced_title, reference_type,
                resolved_document_id, status, evidence_id
            )
            VALUES (%s, %s, %s::reference_type, %s, %s::reference_status, %s)
            RETURNING id
            """,
            (
                source_document_id, referenced_title, reference_type,
                resolved_document_id, status, evidence_id,
            ),
        ).fetchone()
        return row[0]

    def persist_engine(self, documents: list[ParsedDocument]) -> dict[str, int]:
        """Persist a whole ingestion batch atomically, then link related documents."""
        ids: dict[str, int] = {}
        with self.connection.transaction():
            for document in documents:
                ids[document.document_id] = self.persist_document(document)

            base_documents = {
                document.contract_number: document
                for document in documents
                if document.contract_number and document.document_type == "contract"
            }
            for document in documents:
                base = base_documents.get(document.contract_number)
                if not base or base.document_id == document.document_id:
                    continue
                relation = {
                    "amendment_signed": "amends",
                    "amendment_draft": "amends",
                    "supplier_email": "correspondence_about",
                    "appendix": "appendix_of",
                }.get(document.document_type)
                if relation:
                    self.link_documents(ids[document.document_id], ids[base.document_id], relation)

        return ids
