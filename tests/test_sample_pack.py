from datetime import date
from pathlib import Path

from src.engine import ContractEngine
from src.tools import build_tools


def test_sample_pack_resolves_all_required_results():
    root = Path(__file__).resolve().parents[1]
    sources = [(p.name, p.read_bytes()) for p in sorted((root / "documents").glob("*.txt"))]
    engine = ContractEngine.from_sources(sources, root / "config" / "rules.json")
    snapshots = {item.contract_number: item for item in engine.snapshots(date(2026, 3, 10))}

    assert set(snapshots) == {"STN-101", "STN-102", "STN-103"}
    assert snapshots["STN-101"].amount.value == 138_000_000
    assert snapshots["STN-101"].delivery_date.value == date(2026, 4, 30)
    assert snapshots["STN-102"].amount.value == 84_000_000
    assert snapshots["STN-102"].delivery_date.value == date(2026, 3, 20)
    assert snapshots["STN-103"].amount.value is None
    assert snapshots["STN-103"].delivery_date.value == date(2026, 4, 30)

    email = next(d for d in engine.documents if d.document_type == "supplier_email")
    assert email.contract_number == "STN-102"
    assert email.fields["delivery_date"].value == date(2026, 3, 27)


def test_early_delivery_tool_includes_pending_change():
    root = Path(__file__).resolve().parents[1]
    sources = [(p.name, p.read_bytes()) for p in sorted((root / "documents").glob("*.txt"))]
    engine = ContractEngine.from_sources(sources, root / "config" / "rules.json")
    schemas, registry = build_tools(engine, date(2026, 3, 10))

    assert "find_early_deliveries" in registry
    result = registry["find_early_deliveries"]()
    assert [row["contract_number"] for row in result["contracts"]] == ["STN-102"]
    pending = result["contracts"][0]["unapproved_delivery_changes"]
    assert len(pending) == 1
    assert pending[0]["proposed_value"] == "2026-03-27"
    assert any(item["function"]["name"] == "find_early_deliveries" for item in schemas)


def test_future_effective_signed_amendment_does_not_apply_early():
    root = Path(__file__).resolve().parents[1]
    base = (root / "documents" / "01_contract.txt").read_bytes()
    future = ("future.txt", """ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 3 К ДОГОВОРУ STN-101
Дата: 15.03.2026
Статус: подписано обеими сторонами 15.03.2026.
Покупатель: ООО «СеверМет». Поставщик: ООО «Вектор Прибор».

Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить оборудование не позднее 20.05.2026».

Настоящее соглашение вступает в силу 20.03.2026.""".encode("utf-8"))
    engine = ContractEngine.from_sources([("base.txt", base), future])
    snapshot = engine.snapshot("STN-101", date(2026, 3, 10))
    assert snapshot.delivery_date.value == date(2026, 4, 15)
    assert snapshot.delivery_date.status == "resolved"


def test_unsigned_base_contract_requires_review():
    engine = ContractEngine.from_sources([("contract.txt", """ДОГОВОР ПОСТАВКИ № STN-777

1.1. Поставщик: ООО «Проверка».
2.1. Общая стоимость поставки составляет 10 000 рублей.
3.1. Поставщик обязуется поставить товар не позднее 30.04.2026.""".encode("utf-8"))])
    snapshot = engine.snapshot("STN-777", date(2026, 3, 10))
    assert snapshot.amount.value is None
    assert snapshot.amount.status == "needs_review"
