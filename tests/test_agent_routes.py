from datetime import date
from pathlib import Path

from src.agent import LocalAgent
from src.engine import ContractEngine


def sample_engine():
    root = Path(__file__).resolve().parents[1]
    sources = [(p.name, p.read_bytes()) for p in sorted((root / "documents").glob("*.txt"))]
    return ContractEngine.from_sources(sources, root / "config" / "rules.json")


def test_percentage_question_uses_deterministic_history_and_evidence():
    engine = sample_engine()
    agent = LocalAgent()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("Percentage calculation must not call Ollama.")

    agent._chat = unexpected_llm_call
    result = agent.run(
        "На сколько процентов увеличилась сумма договора STN-101 после подписания дополнительного соглашения?",
        engine,
        date(2026, 3, 10),
        history=[
            {"role": "user", "content": "Какие поставщики предлагают перенести сроки поставки?"},
            {"role": "assistant", "content": "По STN-102 поставщик предлагает перенос срока."},
        ],
    )

    assert "увеличилась на 15%" in result.answer
    assert "1 200 000 ₽" in result.answer
    assert "1 380 000 ₽" in result.answer
    assert "01_contract.txt" in result.answer
    assert "02_amendment_signed.txt" in result.answer
    assert result.trace[0]["tool"] == "trace_field"


def test_percentage_question_without_contract_asks_for_clarification():
    engine = sample_engine()
    agent = LocalAgent()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("Clarification must not call Ollama.")

    agent._chat = unexpected_llm_call
    result = agent.run("Посчитай процентное изменение суммы договора", engine, date(2026, 3, 10))

    assert "Уточните номер договора" in result.answer
    assert result.trace == []


def test_trace_field_returns_evidence_for_base_and_applied_amendment():
    engine = sample_engine()
    result = engine.trace_field("STN-101", "amount", date(2026, 3, 10))
    field = result["result"]

    assert result["status"] == "ok"
    assert field["status"] == "resolved"
    assert [item["value"] for item in field["history"]] == [120_000_000, 138_000_000]
    assert field["history"][0]["evidence"]["filename"] == "01_contract.txt"
    assert field["history"][1]["evidence"]["filename"] == "02_amendment_signed.txt"


def test_vague_percentage_request_asks_what_to_calculate():
    engine = sample_engine()
    agent = LocalAgent()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("Vague percentage requests must be clarified without Ollama.")

    agent._chat = unexpected_llm_call
    result = agent.run("Посчитай ченить в процентах", engine, date(2026, 3, 10))

    assert "какой показатель" in result.answer
    assert result.trace == []


def test_cost_proposal_route_requires_contract_number():
    engine = sample_engine()
    agent = LocalAgent()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("A contract number is required before cost generation.")

    agent._generate_cost_options = unexpected_llm_call
    result = agent.run("Предложи стоимость", engine, date(2026, 3, 10))

    assert "Уточните номер договора" in result.answer
    assert result.options == []


def test_cost_proposal_route_calls_structured_generator_for_missing_amount():
    engine = sample_engine()
    agent = LocalAgent()
    expected = [{"label": "Экономный", "amount_rub": 100000, "rationale": "Тест", "assumptions": ["Тестовое допущение"], "risks": "Нужна спецификация"}]

    def fake_generator(contract_number, engine, as_of_date):
        assert contract_number == "STN-103"
        return __import__("src.agent", fromlist=["AgentResult"]).AgentResult(
            answer="Сценарии подготовлены", options=expected * 3
        )

    agent._generate_cost_options = fake_generator
    result = agent.run("Предложи стоимость 103", engine, date(2026, 3, 10))

    assert len(result.options) == 3
    assert "Сценарии подготовлены" in result.answer


def test_cost_proposal_does_not_override_existing_contract_amount():
    engine = sample_engine()
    agent = LocalAgent()

    def unexpected_llm_call(*args, **kwargs):
        raise AssertionError("Existing contract price should be checked before generation.")

    agent._generate_cost_options = unexpected_llm_call
    result = agent.run("Предложи стоимость STN-102", engine, date(2026, 3, 10))

    assert "840 000 ₽" in result.answer
    assert result.options == []


def test_answerability_assessment_distinguishes_missing_attachment_from_missing_value():
    agent = LocalAgent()
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"message": {"content": '{"status":"unanswerable","answer":"Стоимость не определена.","reason":"Договор отсылает к отсутствующему приложению № 1.","missing_information":["Загрузить приложение № 1 или подписанную спецификацию."]}'}}

    def fake_post(url, json, timeout):
        captured["payload"] = json
        return FakeResponse()

    import src.agent as agent_module
    original = agent_module.requests.post
    agent_module.requests.post = fake_post
    try:
        result = agent._assess_answerability(
            "Какая сумма STN-103?", "Сумма не найдена.",
            [{"tool": "get_contract_snapshot", "result": {"amount": {"status": "not_found", "issues": ["Стоимость определяется приложением № 1"]}}}],
        )
    finally:
        agent_module.requests.post = original

    assert result["status"] == "unanswerable"
    assert "приложение № 1" in result["reason"]
    assert result["missing_information"] == ["Загрузить приложение № 1 или подписанную спецификацию."]
    assert "format" in captured["payload"]


def test_answerability_assessment_marks_empty_trace_as_not_evidence():
    agent = LocalAgent()

    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"message": {"content": '{"status":"unanswerable","answer":"Не могу подтвердить ответ.","reason":"Инструменты не предоставили доказательств.","missing_information":["Уточнить номер договора."]}'}}

    import src.agent as agent_module
    original = agent_module.requests.post
    agent_module.requests.post = lambda *args, **kwargs: FakeResponse()
    try:
        result = agent._assess_answerability("Сколько стоит договор?", "Наверное, 500000 ₽", [])
    finally:
        agent_module.requests.post = original

    assert result["status"] == "unanswerable"
    assert "500000" not in result["answer"]



def test_factual_tool_planning_ignores_previous_chat_history():
    engine = sample_engine()
    agent = LocalAgent()
    captured = []

    def fake_chat(messages, schemas):
        captured.append(list(messages))
        if len(captured) == 1:
            return {"message": {
                "role": "assistant",
                "tool_calls": [{
                    "function": {
                        "name": "get_contract_snapshot",
                        "arguments": {"contract_number": "STN-103", "as_of": "2026-03-10"},
                    }
                }],
            }}
        return {"message": {"role": "assistant", "content": "Стоимость STN-103 не указана."}}

    agent._chat = fake_chat
    agent._assess_answerability = lambda question, candidate, trace: {
        "status": "unanswerable",
        "answer": "Стоимость STN-103 не указана.",
        "reason": "В договоре отсутствует числовая стоимость.",
        "missing_information": ["Нужно приложение № 1 или подписанная спецификация."],
    }
    result = agent.run(
        "Какая сумма договора STN-103?",
        engine,
        date(2026, 3, 10),
        history=[
            {"role": "user", "content": "Какой срок поставки по STN-102?"},
            {"role": "assistant", "content": "Действующий срок — 20.03.2026."},
        ],
    )

    first_messages = captured[0]
    assert [item["role"] for item in first_messages] == ["system", "user"]
    assert first_messages[-1]["content"] == "Какая сумма договора STN-103?"
    assert "STN-102" not in str(first_messages)
    assert result.trace[0]["tool"] == "get_contract_snapshot"
    assert result.trace[0]["arguments"]["contract_number"] == "STN-103"
    assert result.answerability["status"] == "unanswerable"


def test_tool_snapshot_uses_ui_review_date_not_model_supplied_date():
    from src.tools import build_tools

    engine = sample_engine()
    review_date = date(2026, 3, 10)
    schemas, registry = build_tools(engine, review_date)

    snapshot_schema = next(
        item["function"] for item in schemas
        if item["function"]["name"] == "get_contract_snapshot"
    )
    assert "as_of" not in snapshot_schema["parameters"]["properties"]

    # Even if a stale/old client supplies the argument, it must not change the review date.
    result = registry["get_contract_snapshot"]("STN-101", as_of="2023-10-01")
    assert result["as_of_date"] == "2026-03-10"
    assert result["amount"]["value_rubles"] == "1 380 000 RUB"
    assert result["delivery_date"]["value"] == "2026-04-30"


def test_dispatcher_ignores_undeclared_tool_arguments_and_uses_workflow_date():
    engine = sample_engine()
    agent = LocalAgent()
    calls = []

    def fake_chat(messages, schemas):
        calls.append(list(messages))
        if len(calls) == 1:
            return {"message": {
                "role": "assistant",
                "tool_calls": [{
                    "function": {
                        "name": "get_contract_snapshot",
                        "arguments": {
                            "contract_number": "STN-102",
                            "field_name": "delivery_date",
                            "date": "2023-07-15",
                        },
                    }
                }],
            }}
        return {"message": {"role": "assistant", "content": "Срок поставки получен из инструмента."}}

    agent._chat = fake_chat
    agent._assess_answerability = lambda question, candidate, trace: {
        "status": "answerable",
        "answer": candidate,
        "reason": "",
        "missing_information": [],
    }

    result = agent.run("Какой срок поставки по STN-102?", engine, date(2026, 3, 10))

    assert result.trace[0]["tool"] == "get_contract_snapshot"
    assert result.trace[0]["executed_arguments"] == {"contract_number": "STN-102"}
    assert result.trace[0]["ignored_arguments"] == ["date", "field_name"]
    assert result.trace[0]["result"]["as_of_date"] == "2026-03-10"
    assert result.trace[0]["result"]["delivery_date"]["value"] == "2026-03-20"
    tool_message = next(message for message in calls[1] if message.get("role") == "tool")
    assert '"ignored_arguments": ["date", "field_name"]' in tool_message["content"]
