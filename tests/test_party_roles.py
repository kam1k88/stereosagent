from datetime import date

from src.engine import ContractEngine
from src.tools import build_tools


CONTRACT = """ДОГОВОР ПОСТАВКИ № STN-102
Дата: 01.02.2026
Статус: подписан обеими сторонами 01.02.2026.

1.1. Покупатель: ООО «СеверМет». Поставщик: ООО «Контур Электро».
2.1. Общая стоимость поставки составляет 840 000 рублей.
3.1. Поставщик обязуется поставить оборудование не позднее 20.03.2026.
"""

EMAIL = """ПИСЬМО ПОСТАВЩИКА О ПРЕДЛАГАЕМОМ ПЕРЕНОСЕ
Дата: 05.03.2026
От: ООО «Контур Электро»
Кому: ООО «СеверМет»
Тема: договор STN-102

Коллеги, по договору STN-102 предлагаем перенести срок поставки с 20.03.2026 на 27.03.2026 из-за задержки комплектующих. Просим подтвердить согласие и оформить дополнительное соглашение.

По состоянию на 10.03.2026 согласие покупателя не получено, дополнительное соглашение не подписано. Это письмо содержит предложение поставщика, а не согласованное изменение договора.
"""


def make_engine():
    return ContractEngine.from_sources([
        ("contract.txt", CONTRACT.encode("utf-8")),
        ("supplier_email.txt", EMAIL.encode("utf-8")),
    ])


def test_supplier_sender_is_resolved_against_contract_parties():
    engine = make_engine()
    email = next(d for d in engine.documents if d.document_type == "supplier_email")

    assert email.sender == "ООО «Контур Электро»"
    assert email.recipient == "ООО «СеверМет»"
    assert engine.party_role("STN-102", email.sender) == "supplier"
    assert engine.party_role("STN-102", email.recipient) == "buyer"


def test_find_proposed_changes_filters_by_initiator_role_and_keeps_evidence():
    engine = make_engine()
    rows = engine.proposed_changes(
        contract_number=None,
        as_of=date(2026, 3, 10),
        field_name="delivery_date",
        party_role="supplier",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["contract_number"] == "STN-102"
    assert row["initiator"] == "ООО «Контур Электро»"
    assert row["initiator_role"] == "supplier"
    assert row["recipient"] == "ООО «СеверМет»"
    assert row["recipient_role"] == "buyer"
    assert row["proposed_value"] == "2026-03-27"
    assert row["status"] == "PROPOSED"
    assert row["evidence"]["filename"] == "supplier_email.txt"
    assert "20.03.2026 на 27.03.2026" in row["evidence"]["quote"]


def test_party_aware_tool_is_registered_and_returns_proposals():
    engine = make_engine()
    schemas, registry = build_tools(engine, date(2026, 3, 10))

    assert any(
        item["function"]["name"] == "find_proposed_changes"
        for item in schemas
    )
    result = registry["find_proposed_changes"](
        field_name="delivery_date",
        party_role="supplier",
        status="PROPOSED",
    )
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["proposals"][0]["initiator_role"] == "supplier"
