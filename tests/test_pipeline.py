from datetime import date

from src.engine import ContractEngine
from src.extractor import parse_amount_kopecks


def b(text: str) -> bytes:
    return text.encode("utf-8")


def test_parse_amount_to_kopecks():
    assert parse_amount_kopecks("1 380 000") == 138_000_000
    assert parse_amount_kopecks("1 380 000,50") == 138_000_050
    assert parse_amount_kopecks("not money") is None


def test_signed_amendment_applies_but_draft_does_not():
    sources = [
        ("random-name-a.txt", b("""ДОГОВОР ПОСТАВКИ № STN-101
Дата: 05.02.2026
Статус: подписан обеими сторонами 05.02.2026.

1.1. Покупатель: ООО «СеверМет». Поставщик: ООО «Вектор Прибор».

2.1. Общая стоимость поставки составляет 1 200 000 рублей, включая НДС.

3.1. Поставщик обязуется поставить оборудование не позднее 15.04.2026.""")),
        ("random-name-b.txt", b("""ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ STN-101
Дата: 01.03.2026
Статус: подписано обеими сторонами 01.03.2026.
Покупатель: ООО «СеверМет». Поставщик: ООО «Вектор Прибор».

1. Пункт 2.1 договора изложить в следующей редакции: «Общая стоимость поставки составляет 1 380 000 рублей, включая НДС».

2. Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить оборудование не позднее 30.04.2026».

4. Настоящее соглашение вступает в силу 01.03.2026.""")),
        ("random-name-c.txt", b("""ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ № 2 К ДОГОВОРУ STN-101
Статус: проект на согласовании, не подписан ни одной стороной.

1. Предлагается изменить общую стоимость поставки на 1 450 000 рублей.
2. Предлагается изменить срок поставки на 10.05.2026.""")),
    ]
    engine = ContractEngine.from_sources(sources)
    snap = engine.snapshot("STN-101", date(2026, 3, 10))
    assert snap.amount.value == 138_000_000
    assert snap.delivery_date.value == date(2026, 4, 30)
    assert snap.amount.changed_by == "random-name-b.txt"
    assert len(snap.proposed_changes) == 2
    assert snap.delivery_date.evidence.paragraph_number > 0


def test_missing_contract_amount_is_none_not_budget_limit():
    sources = [
        ("contract.txt", b("""ДОГОВОР ПОСТАВКИ № STN-103
Дата: 20.02.2026
Статус: подписан обеими сторонами 20.02.2026.

1.1. Покупатель: ООО «СеверМет». Поставщик: ООО «ПромСнаб Сервис».

2.1. Общая стоимость определяется спецификацией в приложении № 1. Приложение № 1 в комплект документов не входит. Числовое значение стоимости в тексте договора не указано.
2.2. Лимит закупки по внутреннему бюджету покупателя — 600 000 рублей. Этот лимит не является согласованной стоимостью договора.

3.1. Поставщик обязуется поставить запасные части не позднее 30.04.2026."""))
    ]
    engine = ContractEngine.from_sources(sources)
    snap = engine.snapshot("STN-103", date(2026, 3, 10))
    assert snap.amount.value is None
    assert snap.delivery_date.value == date(2026, 4, 30)


def test_email_proposal_does_not_change_delivery_date():
    sources = [
        ("contract.txt", b("""ДОГОВОР ПОСТАВКИ № STN-102
Дата: 10.02.2026
Статус: подписан обеими сторонами 10.02.2026.

1.1. Покупатель: ООО «СеверМет». Поставщик: ООО «Контур Электро».

2.1. Общая стоимость поставки составляет 840 000 рублей, включая НДС.
2.2. Авансовый платеж составляет 252 000 рублей и входит в общую стоимость.

3.1. Поставщик обязуется поставить комплектующие не позднее 20.03.2026.
3.2. Оплата оставшейся суммы производится не позднее 10.04.2026. Этот срок является сроком оплаты, а не поставки.""")),
        ("mail.txt", b("""ПИСЬМО ПОСТАВЩИКА О ПРЕДЛАГАЕМОМ ПЕРЕНОСЕ
Дата: 05.03.2026
От: ООО «Контур Электро»
Кому: ООО «СеверМет»
Тема: договор STN-102

Коллеги, по договору STN-102 предлагаем перенести срок поставки с 20.03.2026 на 27.03.2026 из-за задержки комплектующих. Просим подтвердить согласие и оформить дополнительное соглашение.

По состоянию на 10.03.2026 согласие покупателя не получено, дополнительное соглашение не подписано.""")),
    ]
    engine = ContractEngine.from_sources(sources)
    snap = engine.snapshot("STN-102", date(2026, 3, 10))
    assert snap.amount.value == 84_000_000
    assert snap.delivery_date.value == date(2026, 3, 20)
    assert any(p.field_name == "delivery_date" for p in snap.proposed_changes)
    assert any(d.document_type == "supplier_email" for d in engine.documents)


def test_new_contract_with_arbitrary_filename_is_discovered():
    source = ("anything-uploaded.txt", b("""ДОГОВОР ПОСТАВКИ № STN-999
Дата: 01.03.2026
Статус: подписан обеими сторонами 01.03.2026.

1.1. Покупатель: ООО «Альфа». Поставщик: ООО «Новый поставщик».

2.1. Общая стоимость поставки составляет 77 500 рублей.
3.1. Поставщик обязуется поставить товар не позднее 15.05.2026."""))
    engine = ContractEngine.from_sources([source])
    assert engine.contract_numbers() == ["STN-999"]
    snap = engine.snapshot("STN-999", date(2026, 3, 10))
    assert snap.amount.value == 7_750_000
