from __future__ import annotations

import html
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.agent import LocalAgent
from src.engine import ContractEngine
from src.ingestion import sample_sources, uploaded_to_sources

ROOT = Path(__file__).parent
DEFAULT_AS_OF = date(2026, 3, 10)


def rubles(kopecks: int | None) -> str:
    if kopecks is None:
        return "Нет данных"
    whole, cents = divmod(kopecks, 100)
    formatted = f"{whole:,}".replace(",", " ")
    return f"{formatted},{cents:02d} ₽" if cents else f"{formatted} ₽"


def evidence_markdown(field, label: str) -> None:
    if not field.evidence:
        st.caption(f"{label}: нет проверяемого основания.")
        return
    ev = field.evidence
    anchor = f"doc-{ev.document_id}-p-{ev.paragraph_number}"
    st.markdown(f"**{label}**")
    st.caption(f"{ev.filename} · абзац {ev.paragraph_number}")
    st.write(f"«{ev.quote}»")
    st.markdown(f'<a href="#{anchor}">Перейти к исходному абзацу ↓</a>', unsafe_allow_html=True)


def render_cost_options(options: list[dict], message_index: int) -> None:
    if not options:
        return
    st.markdown("**Варианты предварительной стоимости**")
    columns = st.columns(len(options))
    for index, (column, option) in enumerate(zip(columns, options)):
        with column:
            st.markdown(f"**{option['label']}**")
            st.subheader(f"{option['amount_rub']:,.0f} ₽".replace(",", " "))
            st.write(option["rationale"])
            with st.expander("Допущения и риски"):
                for assumption in option["assumptions"]:
                    st.write(f"• {assumption}")
                st.warning(option["risks"])
            if st.button("Выбрать вариант", key=f"cost_option_{message_index}_{index}", use_container_width=True):
                st.session_state[f"selected_cost_option_{message_index}"] = option
                st.session_state[f"selected_cost_option_notice_{message_index}"] = True
    selected = st.session_state.get(f"selected_cost_option_{message_index}")
    if selected:
        st.success(
            f"Вы выбрали: {selected['label']} — {selected['amount_rub']:,.0f} ₽. "
            "Это предварительный вариант для обсуждения; договор не изменён."
        )


def quick_answer(engine: ContractEngine, question_id: int, as_of: date) -> str:
    if question_id == 1:
        snap = engine.snapshot("STN-101", as_of)
        value = snap.delivery_date.value
        if value is None:
            return "По STN-101 срок поставки не удалось определить."
        ev = snap.delivery_date.evidence
        if ev:
            return (
                f"По STN-101 на {as_of:%d.%m.%Y} действует срок поставки "
                f"**{value:%d.%m.%Y}**. Источник: **{ev.filename}**, абзац {ev.paragraph_number}.\n\n"
                f"> {ev.quote}"
            )
        return f"Срок: {value:%d.%m.%Y}; проверяемый источник не найден."
    if question_id == 2:
        snap = engine.snapshot("STN-102", as_of)
        ev = snap.amount.evidence
        if snap.amount.value is None:
            return "По STN-102: **Нет данных**."
        return f"Сумма STN-102: **{rubles(snap.amount.value)}**.\n\nИсточник: {ev.filename}, абзац {ev.paragraph_number}.\n\n> {ev.quote}"
    if question_id == 3:
        snap = engine.snapshot("STN-103", as_of)
        return (
            "По STN-103: **Нет данных**. Стоимость определяется спецификацией, "
            "которой нет в комплекте. Внутренний бюджетный лимит не является ценой договора."
        )
    cutoff = date(2026, 4, 1)
    early = []
    for snap in engine.snapshots(as_of):
        value = snap.delivery_date.value
        if value is not None and value < cutoff:
            proposed = [p for p in snap.proposed_changes if p.field_name == "delivery_date"]
            early.append((snap.contract_number, value, proposed))
    if not early:
        return "Договоров с поставкой раньше 01.04.2026 не найдено."
    parts = ["**Поставка раньше 01.04.2026:**"]
    for number, value, proposals in early:
        parts.append(f"- **{number}** — {value:%d.%m.%Y}.")
        if proposals:
            for proposal in proposals:
                parts.append(
                    f"  - Несогласованное предложение: {proposal.proposed_value:%d.%m.%Y} "
                    f"({proposal.filename}, абзац {proposal.evidence.paragraph_number})."
                )
        else:
            parts.append("  - Несогласованных предложений срока не найдено.")
    return "\n".join(parts)


st.set_page_config(page_title="Semantic TextNavigator", page_icon="📄", layout="wide")
st.title("Semantic TextNavigator")
st.caption("Локальный агент для проверки условий договоров поставки")

with st.sidebar:
    st.header("Параметры")
    as_of = st.date_input("Дата проверки", value=DEFAULT_AS_OF)
    ollama_host = st.text_input(
        "Ollama URL", value=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11500")
    )
    ollama_model = st.text_input(
        "Модель", value=os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    )
    uploaded = st.file_uploader(
        "Загрузить TXT-документы",
        type=["txt"],
        accept_multiple_files=True,
        help="Имена файлов не используются для определения их типа или приоритета.",
    )
    use_samples = st.checkbox(
        "Использовать демонстрационный комплект, если файлы не загружены",
        value=True,
    )

if uploaded:
    sources = uploaded_to_sources(uploaded)
    source_mode = "Загруженные документы"
elif use_samples:
    sources = sample_sources(ROOT / "documents")
    source_mode = "Демонстрационный комплект"
else:
    sources = []
    source_mode = "Нет документов"

if not sources:
    st.info("Загрузите несколько TXT-файлов или включите демонстрационный комплект.")
    st.stop()

try:
    engine = ContractEngine.from_sources(sources, ROOT / "config" / "rules.json")
except Exception as exc:
    st.error(f"Не удалось обработать документы: {exc}")
    st.stop()

# Optional persistence. The default demo remains fully in-memory.
if os.getenv("STEREOSAGENT_PERSISTENCE", "").strip().lower() == "postgres":
    persisted_hashes = st.session_state.setdefault("postgres_persisted_hashes", set())
    pending_documents = [
        doc for doc in engine.documents if doc.content_hash not in persisted_hashes
    ]
    if pending_documents:
        try:
            from src.database import PostgresRepository
            from src.database.connection import connect

            with connect() as connection:
                PostgresRepository(connection).persist_engine(pending_documents)
            persisted_hashes.update(doc.content_hash for doc in pending_documents)
            st.caption(f"PostgreSQL: сохранено новых документов — {len(pending_documents)}.")
        except Exception as exc:
            st.error(
                "Не удалось сохранить документы в PostgreSQL. "
                "Текущая проверка продолжает работать в памяти; "
                f"проверьте DATABASE_URL и применённую schema.sql. Причина: {exc}"
            )

st.caption(
    f"{source_mode} · обработано файлов: {len(engine.documents)} · "
    f"обнаружено договоров: {len(engine.contract_numbers())}"
)

tab_overview, tab_chat, tab_documents = st.tabs(
    ["Обзор договоров", "Чат с агентом", "Исходные документы"]
)

with tab_overview:
    rows = []
    snapshots = engine.snapshots(as_of)
    for snap in snapshots:
        delivery = snap.delivery_date.value
        rows.append({
            "Договор": snap.contract_number,
            "Поставщик": snap.supplier or "Нет данных",
            "Сумма": rubles(snap.amount.value),
            "Срок поставки": delivery.strftime("%d.%m.%Y") if delivery else "Нет данных",
            "Статус суммы": snap.amount.status,
            "Статус срока": snap.delivery_date.status,
        })
    st.subheader(f"Действующие условия на {as_of:%d.%m.%Y}")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Проверяемые основания")
    if not snapshots:
        st.warning("Базовые договоры не обнаружены.")
    for snap in snapshots:
        with st.expander(f"{snap.contract_number} · {snap.supplier or 'Поставщик не определён'}"):
            col1, col2 = st.columns(2)
            with col1:
                evidence_markdown(snap.amount, f"Сумма: {rubles(snap.amount.value)}")
                if snap.amount.changed_by:
                    st.info(f"Изменено документом: {snap.amount.changed_by}")
                for issue in snap.amount.issues:
                    st.warning(issue)
            with col2:
                delivery_text = snap.delivery_date.value.strftime("%d.%m.%Y") if snap.delivery_date.value else "Нет данных"
                evidence_markdown(snap.delivery_date, f"Срок поставки: {delivery_text}")
                if snap.delivery_date.changed_by:
                    st.info(f"Изменено документом: {snap.delivery_date.changed_by}")
                for issue in snap.delivery_date.issues:
                    st.warning(issue)
            for issue in snap.issues:
                st.warning(issue)

    proposed_rows = engine.proposed_changes(None, as_of)
    st.subheader("Несогласованные изменения")
    if proposed_rows:
        display_rows = []
        for item in proposed_rows:
            value = item["proposed_value"]
            if item["field"] == "amount" and value is not None:
                value = rubles(value)
            display_rows.append({
                "Договор": item["contract_number"],
                "Документ": item["filename"],
                "Тип": item["document_type"],
                "Поле": item["field"],
                "Предлагаемое значение": value,
                "Абзац": item["evidence"]["paragraph_number"],
            })
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
        for item in proposed_rows:
            with st.expander(f"{item['contract_number']} · {item['filename']} · {item['field']}"):
                st.write(item["evidence"]["quote"])
    else:
        st.success("Несогласованных предложений в загруженных документах не найдено.")

    st.subheader("Быстрые проверки")
    st.caption("Работают без LLM и позволяют проверить обязательные вопросы при недоступной Ollama.")
    quick_cols = st.columns(4)
    labels = ["Срок STN-101", "Сумма STN-102", "Сумма STN-103", "Поставка до 01.04"]
    for index, col in enumerate(quick_cols, start=1):
        if col.button(labels[index - 1], use_container_width=True):
            st.session_state["quick_answer"] = quick_answer(engine, index, as_of)
    if st.session_state.get("quick_answer"):
        st.info(st.session_state["quick_answer"])

with tab_chat:
    st.subheader("Задайте вопрос о загруженных договорах")
    st.caption(
        "Агент использует семь разрешённых инструментов. Бизнес-правила и определение "
        "действующих условий выполняет Python workflow; предварительные варианты стоимости "
        "LLM генерирует отдельно, а пользователь подтверждает выбор."
    )
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    for item in st.session_state.chat_messages:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            if item.get("options"):
                render_cost_options(item["options"], item.get("message_index", 0))
            if item.get("answerability", {}).get("status") in ("partial", "unanswerable"):
                st.info("Проверка достаточности данных: " + item["answerability"]["status"])
            if item.get("trace"):
                with st.expander("Трассировка инструментов"):
                    for event in item["trace"]:
                        st.markdown(f"**Шаг {event['step']} · {event['tool']}**")
                        st.json({"arguments": event["arguments"], "result": event["result"]})

    prompt = st.chat_input("Например: почему по STN-101 действует срок 30 апреля?")
    if prompt:
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner(f"Агент обращается к {ollama_model}..."):
                try:
                    agent = LocalAgent(host=ollama_host, model=ollama_model, max_steps=5)
                    # Chat history is displayed in the UI but is not sent to factual
                    # tool planning: each contract question must stand on its own evidence.
                    result = agent.run(prompt, engine, as_of)
                    if not result.trace and result.messages and result.answerability.get("status") == "not_assessed":
                        answer = (
                            "Модель не вызвала инструмент, а автоматическая проверка достаточности "
                            "доказательств не завершилась. Ответ не подтверждён. Повторите вопрос; "
                            "для точной суммы или срока можно воспользоваться быстрыми проверками "
                            "на вкладке «Обзор договоров»."
                        )
                    else:
                        answer = result.answer
                    st.markdown(answer)
                    message_index = len(st.session_state.chat_messages)
                    if result.options:
                        render_cost_options(result.options, message_index)
                    if result.trace:
                        with st.expander("Трассировка инструментов"):
                            for event in result.trace:
                                st.markdown(f"**Шаг {event['step']} · {event['tool']}**")
                                st.json({"arguments": event["arguments"], "result": event["result"]})
                    st.session_state.chat_messages.append({
                        "role": "assistant", "content": answer, "trace": result.trace,
                        "options": result.options, "message_index": message_index,
                        "answerability": result.answerability
                    })
                except Exception as exc:
                    message = (
                        f"Не удалось обратиться к Ollama: {type(exc).__name__}: {exc}\n\n"
                        f"Проверьте, что Ollama запущена по адресу {ollama_host}, "
                        f"а модель {ollama_model} загружена. Быстрые проверки на вкладке "
                        "«Обзор договоров» работают без LLM."
                    )
                    st.error(message)
                    st.session_state.chat_messages.append(
                        {"role": "assistant", "content": message}
                    )

    if st.button("Очистить историю чата"):
        st.session_state.chat_messages = []
        st.rerun()

with tab_documents:
    st.subheader("Исходные документы и переход к абзацам")
    st.caption("Якоря позволяют перейти к конкретному абзацу, на который ссылается доказательство.")
    for doc in engine.documents:
        with st.expander(
            f"{doc.filename} · {doc.document_type} · {doc.contract_number or 'договор не определён'}"
        ):
            for issue in doc.issues:
                st.warning(issue)
            for number, paragraph in enumerate(doc.paragraphs, start=1):
                anchor = f"doc-{doc.document_id}-p-{number}"
                safe_text = html.escape(paragraph)
                st.markdown(
                    f'<div id="{anchor}" style="padding:8px 0;border-bottom:1px solid #ddd">'
                    f'<b>Абзац {number}</b><pre style="white-space:pre-wrap">{safe_text}</pre></div>',
                    unsafe_allow_html=True,
                )
