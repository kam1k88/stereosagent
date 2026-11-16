
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ..ingestion import split_paragraphs
from ..models import Evidence, ParsedDocument


def _json_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    return json.dumps(value, ensure_ascii=False, default=str)


class PostgresRepository:
    """Persistence adapter for parsed documents and their source evidence.

    Extracted assertions are stored separately from legally resolved
    contract conditions. The resolver remains the source of truth.
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
            ON CONFLICT (lower(name)) DO UPDATE
                SET name = EXCLUDED.name
            RETURNING id
            """,
            (name.strip(),),
        ).fetchone()

        return row[0]

    def _contract_id(
        self,
        contract_number: str,
        buyer_id: int | None,
        supplier_id: int | None,
    ) -> int:
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

    def _evidence_id(self, document_db_id: int, evidence: Evidence) -> int:
        row = self.connection.execute(
            """
            INSERT INTO evidence (document_id, paragraph_number, quote)
            VALUES (%s, %s, %s)
            ON CONFLICT (document_id, paragraph_number, quote)
            DO UPDATE SET quote = EXCLUDED.quote
            RETURNING id
            """,
            (
                document_db_id,
                evidence.paragraph_number,
                evidence.quote,
            ),
        ).fetchone()

        return row[0]

    def persist_document(self, document: ParsedDocument) -> int:
        """Upsert one parsed document, its parties, facts and evidence."""
        buyer_id = self._party_id(document.buyer)
        supplier_id = self._party_id(document.supplier)

        contract_id = None
        if document.contract_number:
            contract_id = self._contract_id(
                document.contract_number,
                buyer_id,
                supplier_id,
            )

        metadata = {
            "signed_by_both": document.signed_by_both,
            "issues": document.issues,
        }

        row = self.connection.execute(
            """
            INSERT INTO documents (
                external_id,
                contract_id,
                filename,
                document_type,
                content_hash,
                content,
                sender,
                recipient,
                signature_date,
                effective_date,
                metadata
            )
            VALUES (
                %s, %s, %s, %s::document_type, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
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
                document.document_id,
                contract_id,
                document.filename,
                document.document_type,
                document.content_hash,
                document.text,
                document.sender,
                document.recipient,
                document.signature_date,
                document.effective_date,
                json.dumps(metadata, ensure_ascii=False),
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
                INSERT INTO document_facts (
                    document_id,
                    field_name,
                    value_json,
                    evidence_id
                )
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (document_id, field_name) DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    evidence_id = EXCLUDED.evidence_id,
                    extracted_at = now()
                """,
                (
                    document_db_id,
                    field_name,
                    _json_value(evidence.value),
                    evidence_id,
                ),
            )

        if document.signed_by_both is True and document.signature_date:
            for party_id in (buyer_id, supplier_id):
                if party_id is None:
                    continue

                self.connection.execute(
                    """
                    INSERT INTO document_signatures (
                        document_id,
                        party_id,
                        status,
                        signed_at
                    )
                    VALUES (%s, %s, 'signed', %s)
                    ON CONFLICT (document_id, party_id) DO UPDATE SET
                        status = 'signed',
                        signed_at = EXCLUDED.signed_at
                    """,
                    (
                        document_db_id,
                        party_id,
                        document.signature_date,
                    ),
                )

        return document_db_id

    def link_documents(
        self,
        source_document_id: int,
        target_document_id: int,
        relation: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO document_relations (
                source_document_id,
                target_document_id,
                relation
            )
            VALUES (%s, %s, %s::relation_type)
            ON CONFLICT (
                source_document_id,
                target_document_id,
                relation
            ) DO NOTHING
            """,
            (
                source_document_id,
                target_document_id,
                relation,
            ),
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
            raise ValueError(
                "Для resolved-ссылки нужен resolved_document_id."
            )

        if status != "resolved" and resolved_document_id is not None:
            raise ValueError(
                "resolved_document_id допустим только для resolved-ссылки."
            )

        row = self.connection.execute(
            """
            INSERT INTO document_references (
                source_document_id,
                referenced_title,
                reference_type,
                resolved_document_id,
                status,
                evidence_id
            )
            VALUES (%s, %s, %s::reference_type, %s, %s::reference_status, %s)
            RETURNING id
            """,
            (
                source_document_id,
                referenced_title,
                reference_type,
                resolved_document_id,
                status,
                evidence_id,
            ),
        ).fetchone()

        return row[0]

    def persist_engine(
        self,
        documents: list[ParsedDocument],
    ) -> dict[str, int]:
        """Persist an ingestion batch atomically and link related documents."""
        ids: dict[str, int] = {}

        with self.connection.transaction():
            for document in documents:
                ids[document.document_id] = self.persist_document(document)

            base_documents: dict[str, list[ParsedDocument]] = {}

            for item in documents:
                if (
                    item.contract_number
                    and item.document_type == "contract"
                ):
                    base_documents.setdefault(
                        item.contract_number, []
                    ).append(item)

            for document in documents:
                relation = {
                    "amendment_signed": "amends",
                    "amendment_draft": "amends",
                    "supplier_email": "correspondence_about",
                    "appendix": "appendix_of",
                }.get(document.document_type)

                if not relation or not document.contract_number:
                    continue

                bases = base_documents.get(document.contract_number, [])

                if len(bases) > 1:
                    continue

                if len(bases) == 1:
                    base_db_id = ids[bases[0].document_id]
                else:
                    rows = self.connection.execute(
                        """
                        SELECT d.id
                        FROM documents AS d
                        JOIN contracts AS c ON c.id = d.contract_id
                        WHERE c.contract_number = %s
                          AND d.document_type = 'contract'
                        ORDER BY d.id
                        LIMIT 2
                        """,
                        (document.contract_number,),
                    ).fetchall()

                    if len(rows) != 1:
                        continue

                    base_db_id = rows[0][0]

                source_db_id = ids[document.document_id]

                if source_db_id != base_db_id:
                    self.link_documents(
                        source_db_id,
                        base_db_id,
                        relation,
                    )

        return ids

    @staticmethod
    def _restore_fact_value(field_name: str, value: Any) -> Any:
        """Restore Python types serialized into JSONB."""
        if field_name == "delivery_date" and isinstance(value, str):
            return date.fromisoformat(value)

        return value

    def load_documents(self) -> list[ParsedDocument]:
        """Restore ParsedDocument objects from PostgreSQL without re-parsing."""
        rows = self.connection.execute(
            """
            SELECT
                d.id,
                d.external_id,
                d.filename,
                d.content_hash,
                d.content,
                d.document_type,
                d.sender,
                d.recipient,
                d.signature_date,
                d.effective_date,
                d.metadata,
                c.contract_number,
                p_supplier.name,
                p_buyer.name
            FROM documents AS d
            LEFT JOIN contracts AS c
                ON c.id = d.contract_id
            LEFT JOIN parties AS p_supplier
                ON p_supplier.id = c.supplier_id
            LEFT JOIN parties AS p_buyer
                ON p_buyer.id = c.buyer_id
            ORDER BY d.id
            """
        ).fetchall()

        documents: list[ParsedDocument] = []

        for row in rows:
            (
                db_id,
                external_id,
                filename,
                content_hash,
                content,
                document_type,
                sender,
                recipient,
                signature_date,
                effective_date,
                metadata,
                contract_number,
                supplier,
                buyer,
            ) = row

            metadata = metadata or {}

            fact_rows = self.connection.execute(
                """
                SELECT
                    f.field_name,
                    f.value_json,
                    e.paragraph_number,
                    e.quote
                FROM document_facts AS f
                LEFT JOIN evidence AS e
                    ON e.id = f.evidence_id
                WHERE f.document_id = %s
                ORDER BY f.field_name
                """,
                (db_id,),
            ).fetchall()

            fields: dict[str, Evidence] = {}
            supplier_evidence = None
            buyer_evidence = None

            for (
                field_name,
                raw_value,
                paragraph_number,
                quote,
            ) in fact_rows:

                if paragraph_number is None or quote is None:
                    raise ValueError(
                        f"Факт {field_name!r} документа {filename!r} "
                        "не имеет связанного evidence."
                    )

                value = self._restore_fact_value(
                    field_name,
                    raw_value,
                )

                evidence = Evidence(
                    value=value,
                    filename=filename,
                    paragraph_number=paragraph_number,
                    quote=quote,
                    document_id=external_id,
                )

                if field_name == "supplier":
                    supplier_evidence = evidence
                elif field_name == "buyer":
                    buyer_evidence = evidence
                else:
                    fields[field_name] = evidence

            documents.append(
                ParsedDocument(
                    document_id=external_id,
                    filename=filename,
                    content_hash=content_hash.strip(),
                    text=content,
                    paragraphs=split_paragraphs(content),
                    document_type=str(document_type),
                    contract_number=contract_number,
                    supplier=supplier,
                    supplier_evidence=supplier_evidence,
                    buyer=buyer,
                    buyer_evidence=buyer_evidence,
                    sender=sender,
                    recipient=recipient,
                    signature_date=signature_date,
                    effective_date=effective_date,
                    signed_by_both=metadata.get("signed_by_both"),
                    fields=fields,
                    issues=metadata.get("issues") or [],
                )
            )

        return documents
