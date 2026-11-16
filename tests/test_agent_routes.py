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
