from __future__ import annotations

from datetime import date
from typing import Any

from .models import ContractSnapshot, ParsedDocument, ProposedChange, ResolvedField


def _is_applicable(doc: ParsedDocument, as_of: date, rules: dict[str, Any]) -> tuple[bool, str]:
    policy = rules["amendment"]
    if doc.document_type != "amendment_signed":
        return False, "Тип документа не является подписанным дополнительным соглашением."
    if policy["require_both_signatures"] and doc.signed_by_both is not True:
        return False, "Не подтверждено подписание обеими сторонами."
    if policy["require_signature_date"] and doc.signature_date is None:
        return False, "Не найдена дата подписания."
    if policy["require_effective_date"] and doc.effective_date is None:
        return False, "Не найдена дата вступления в силу."
    if policy["signature_date_lte_as_of"] and doc.signature_date and doc.signature_date > as_of:
        return False, "Дата подписания позже даты проверки."
    if policy["effective_date_lte_as_of"] and doc.effective_date and doc.effective_date > as_of:
        return False, "Дата вступления в силу позже даты проверки."
    return True, "Применимо."


def _resolve_field(base: ParsedDocument | None, amendments: list[ParsedDocument],
                   field_name: str, as_of: date, rules: dict[str, Any]) -> ResolvedField:
    result = ResolvedField()
    blocking_review = False
    if base and base.signed_by_both is True and field_name in base.fields:
        result.value = base.fields[field_name].value
        result.evidence = base.fields[field_name]
        result.status = "resolved"
        result.history.append({
            "source": base.filename,
            "document_id": base.document_id,
            "value": result.value.isoformat() if hasattr(result.value, "isoformat") else result.value,
            "kind": "base_contract",
        })
    elif base and base.signed_by_both is not True:
        blocking_review = True
        result.status = "needs_review"
        result.issues.append("Не подтверждено подписание базового договора обеими сторонами.")
    elif base:
        result.status = "not_found"

    candidates: list[tuple[ParsedDocument, Any]] = []
    for doc in amendments:
        if field_name not in doc.fields:
            continue
        applicable, reason = _is_applicable(doc, as_of, rules)
        if applicable:
            candidates.append((doc, doc.fields[field_name]))
        elif doc.document_type == "amendment_signed":
            future_dated = (
                doc.signed_by_both is True
                and doc.signature_date is not None
                and doc.effective_date is not None
                and (doc.signature_date > as_of or doc.effective_date > as_of)
            )
            if not future_dated:
                blocking_review = True
                result.issues.append(f"{doc.filename}: {reason}")
                result.status = "needs_review"

    candidates.sort(key=lambda pair: (pair[0].effective_date or date.min,
                                      pair[0].signature_date or date.min,
                                      pair[0].filename))

    for left, right in zip(candidates, candidates[1:]):
        if (left[0].effective_date, left[0].signature_date) == (right[0].effective_date, right[0].signature_date):
            if left[1].value != right[1].value:
                result.value = None
                result.evidence = None
                result.changed_by = None
                result.status = "needs_review"
                result.issues.append("Найдены противоречащие изменения с одинаковыми датами.")
                return result

    for doc, evidence in candidates:
        result.value = evidence.value
        result.evidence = evidence
        result.status = "resolved"
        result.changed_by = doc.filename
        result.change_count += 1
        result.history.append({
            "source": doc.filename,
            "document_id": doc.document_id,
            "value": evidence.value.isoformat() if hasattr(evidence.value, "isoformat") else evidence.value,
            "kind": "applicable_amendment",
            "effective_date": doc.effective_date.isoformat() if doc.effective_date else None,
        })

    if blocking_review:
        result.value = None
        result.evidence = None
        result.changed_by = None
        result.status = "needs_review"
    return result


def resolve_contract(contract_number: str, docs: list[ParsedDocument], as_of: date,
                     rules: dict[str, Any]) -> ContractSnapshot:
    related = [d for d in docs if d.contract_number == contract_number]
    contracts = [d for d in related if d.document_type == "contract"]
    base = contracts[0] if len(contracts) == 1 else None
    issues: list[str] = []
    if not contracts:
        issues.append("Не найден базовый договор.")
    elif len(contracts) > 1:
        issues.append("Найдено несколько базовых договоров с одним номером; требуется проверка.")

    amendments = [d for d in related if d.document_type in (
        "amendment_signed", "amendment_draft", "supplier_email"
    )]
    amount = _resolve_field(base, amendments, "amount", as_of, rules)
    delivery = _resolve_field(base, amendments, "delivery_date", as_of, rules)

    proposals: list[ProposedChange] = []
    for doc in amendments:
        if doc.document_type not in rules.get("non_binding_types", []):
            continue
        for field_name, evidence in doc.fields.items():
            proposals.append(ProposedChange(
                contract_number=contract_number,
                filename=doc.filename,
                document_type=doc.document_type,
                field_name=field_name,
                proposed_value=evidence.value,
                evidence=evidence,
            ))

    if len(contracts) > 1:
        amount.status = "needs_review"
        amount.value = None
        amount.evidence = None
        delivery.status = "needs_review"
        delivery.value = None
        delivery.evidence = None

    supplier_doc = base
    supplier = supplier_doc.supplier if supplier_doc else None
    supplier_evidence = supplier_doc.supplier_evidence if supplier_doc else None
    return ContractSnapshot(
        contract_number=contract_number,
        supplier=supplier,
        supplier_evidence=supplier_evidence,
        as_of_date=as_of,
        amount=amount,
        delivery_date=delivery,
        related_documents=[
            {
                "document_id": d.document_id,
                "filename": d.filename,
                "document_type": d.document_type,
                "signed_by_both": d.signed_by_both,
                "signature_date": d.signature_date.isoformat() if d.signature_date else None,
                "effective_date": d.effective_date.isoformat() if d.effective_date else None,
            } for d in related
        ],
        proposed_changes=proposals,
        rule_set_id=rules.get("rule_set_id", "unknown"),
        rule_version=rules.get("version", 1),
        issues=issues,
    )
