from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .classifier import classify_document
from .ingestion import normalize_text, sha256, split_paragraphs
from .models import Evidence, ParsedDocument

DATE_RE = r"(\d{2}\.\d{2}\.\d{4})"
AMOUNT_TOKEN = r"(\d[\d \u00a0\u202f]*(?:[.,]\d{1,2})?)"


def parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_amount_kopecks(value: str) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", value).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    whole, dot, fractional = cleaned.partition(".")
    return int(whole) * 100 + int((fractional + "00")[:2]) if dot else int(whole) * 100


def _evidence(doc_id: str, filename: str, paragraphs: list[str], index: int, value: Any) -> Evidence:
    return Evidence(value, filename, index + 1, paragraphs[index].strip(), doc_id)


def _find_first(paragraphs: list[str], patterns: list[str], converter, doc_id: str, filename: str):
    for index, paragraph in enumerate(paragraphs):
        for pattern in patterns:
            match = re.search(pattern, paragraph, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = converter(match.group(1))
                if value is not None:
                    return _evidence(doc_id, filename, paragraphs, index, value)
    return None


def extract_document(filename: str, raw: bytes) -> ParsedDocument:
    original = raw.decode("utf-8-sig", errors="replace")
    if "\ufffd" in original:
        try:
            original = raw.decode("cp1251")
        except UnicodeDecodeError:
            pass
    text = normalize_text(original)
    paragraphs = split_paragraphs(text)
    digest = sha256(raw)
    document_id = digest[:12]
    doc_type = classify_document(text)

    number_match = re.search(
        r"(?:договор(?:а|у)?(?:\s+поставки)?|к\s+договору|по\s+договору)"
        r"\s*(?:№|N)?\s*(STN[-–]\d+)",
        text,
        re.IGNORECASE,
    )
    contract_number = number_match.group(1).replace("–", "-").upper() if number_match else None

    supplier_evidence = _find_first(
        paragraphs,
        [r"Поставщик\s*:\s*([^\n.]+)"],
        lambda x: x.strip().strip("«»\""),
        document_id,
        filename,
    )
    supplier = supplier_evidence.value if supplier_evidence else None

    signature_date = None
    effective_date = None
    signed_by_both: bool | None = None
    for paragraph in paragraphs:
        if re.search(r"не\s+подписан|подписание\s+не\s+состоялось|не\s+подписано", paragraph, re.IGNORECASE):
            if doc_type in ("amendment_draft", "supplier_email"):
                signed_by_both = False
        if re.search(r"подписан[оы]?\s+обеими\s+сторонами", paragraph, re.IGNORECASE):
            signed_by_both = True
            match = re.search(DATE_RE, paragraph)
            if match:
                signature_date = parse_date(match.group(1))
        if re.search(r"вступает\s+в\s+силу", paragraph, re.IGNORECASE):
            match = re.search(DATE_RE, paragraph)
            if match:
                effective_date = parse_date(match.group(1))

    if doc_type == "contract" and signed_by_both is None:
        signed_by_both = False

    amount_patterns = [
        rf"общая\s+стоимость(?:\s+поставки)?\s+составляет\s*[:—-]?\s*{AMOUNT_TOKEN}",
        rf"общую\s+стоимость(?:\s+поставки)?\s+на\s*[:—-]?\s*{AMOUNT_TOKEN}",
        rf"стоимость\s+поставки\s+составляет\s*[:—-]?\s*{AMOUNT_TOKEN}",
    ]
    delivery_patterns = [
        rf"постав\w{{0,30}}?не\s+позднее\s+{DATE_RE}",
        rf"срок\s+поставки\s+(?:изменить\s+)?на\s+{DATE_RE}",
        rf"перенести\s+срок\s+поставки\s+.{0,60}?\s+на\s+{DATE_RE}",
    ]

    fields: dict[str, Evidence] = {}
    amount_ev = _find_first(paragraphs, amount_patterns, parse_amount_kopecks, document_id, filename)
    if amount_ev:
        fields["amount"] = amount_ev
    delivery_ev = _find_first(paragraphs, delivery_patterns, parse_date, document_id, filename)
    if delivery_ev:
        fields["delivery_date"] = delivery_ev

    issues: list[str] = []
    if not contract_number:
        issues.append("Не удалось определить номер договора.")
    if doc_type == "amendment_signed" and (
        signed_by_both is not True or not signature_date or not effective_date
    ):
        issues.append("Недостаточно подтверждений подписания или даты вступления в силу.")

    return ParsedDocument(
        document_id=document_id,
        filename=filename,
        content_hash=digest,
        text=text,
        paragraphs=paragraphs,
        document_type=doc_type,
        contract_number=contract_number,
        supplier=supplier,
        supplier_evidence=supplier_evidence,
        signature_date=signature_date,
        effective_date=effective_date,
        signed_by_both=signed_by_both,
        fields=fields,
        issues=issues,
    )
