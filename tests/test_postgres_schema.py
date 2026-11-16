from pathlib import Path

import pytest

from src.database import PostgresRepository


class FakeCursor:
    def fetchone(self):
        return (17,)


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return FakeCursor()


def test_repository_imports_without_connecting_to_postgres():
    assert PostgresRepository is not None


def test_missing_referenced_appendix_can_be_recorded():
    connection = FakeConnection()
    repository = PostgresRepository(connection)

    reference_id = repository.add_document_reference(
        source_document_id=3,
        referenced_title="Приложение №1",
        reference_type="appendix",
        status="missing",
    )

    assert reference_id == 17
    assert len(connection.calls) == 1
    assert "document_references" in connection.calls[0][0]
    assert connection.calls[0][1] == (3, "Приложение №1", "appendix", None, "missing", None)


def test_resolved_reference_requires_target_document():
    repository = PostgresRepository(FakeConnection())

    with pytest.raises(ValueError, match="resolved_document_id"):
        repository.add_document_reference(
            source_document_id=3,
            referenced_title="Приложение №1",
            reference_type="appendix",
            status="resolved",
        )


def test_unresolved_reference_cannot_have_target_document():
    repository = PostgresRepository(FakeConnection())

    with pytest.raises(ValueError, match="только для resolved"):
        repository.add_document_reference(
            source_document_id=3,
            referenced_title="Приложение №1",
            reference_type="appendix",
            status="missing",
            resolved_document_id=9,
        )


def test_schema_contains_domain_enums_and_integrity_constraints():
    schema = (
        Path(__file__).resolve().parents[1] / "src" / "database" / "schema.sql"
    ).read_text(encoding="utf-8")

    for token in (
        "CREATE TYPE document_type AS ENUM",
        "'amendment_signed'",
        "'amendment_draft'",
        "'supplier_email'",
        "'unknown'",
        "CREATE TABLE document_relations",
        "CREATE TABLE document_references",
        "CREATE TABLE evidence",
        "CREATE TABLE contract_conditions",
        "REFERENCES contracts(id)",
        "REFERENCES documents(id)",
    ):
        assert token in schema
