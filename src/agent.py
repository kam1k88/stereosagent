from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import requests

from .engine import ContractEngine
from .tools import build_tools


SYSTEM_PROMPT = """Ты — локальный ассистент Semantic TextNavigator для проверки условий договоров поставки.
Работай только с документами текущей сессии и через доступные инструменты.
Для фактов о сумме, сроках, статусе и изменениях ОБЯЗАТЕЛЬНО вызывай инструменты. Не используй память модели как источник фактов.
Суммы из инструментов представлены в рублях и копейках; не называй копейки рублями.
Не придумывай суммы, даты, цитаты, номера абзацев и имена файлов.
Правила применимости дополнительных соглашений реализованы в get_contract_snapshot и trace_field. Не переопределяй их.
Проекты и письма — только предложения, они не меняют действующие условия.
Если status=needs_review, сообщи о необходимости проверки и объясни причины.
Если данных нет, пиши «Нет данных», а не ноль.
Цитируй только evidence, возвращённые инструментами. Указывай имя файла и номер абзаца.
Отвечай по-русски, кратко и структурированно. При неоднозначном вопросе задай один уточняющий вопрос.
"""


@dataclass
class AgentResult:
    answer: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


class LocalAgent:
    """Small bounded tool-calling runtime inspired by modern agent runtimes."""

    def __init__(self, host: str | None = None, model: str | None = None, max_steps: int = 5):
        self.host = (host or os.getenv("OLLAMA_HOST", "http://127.0.0.1:11500")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.max_steps = max(1, min(max_steps, 8))

    def _chat(self, messages: list[dict], tools: list[dict]) -> dict:
        response = requests.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=(5, 180),
        )
        response.raise_for_status()
        return response.json()

    def run(self, user_text: str, engine: ContractEngine, as_of_date,
            history: list[dict] | None = None) -> AgentResult:
        schemas, registry = build_tools(engine, as_of_date)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-10:]:
            if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_text})
        trace: list[dict[str, Any]] = []

        for step in range(1, self.max_steps + 1):
            response = self._chat(messages, schemas)
            assistant = response.get("message") or {}
            messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                answer = assistant.get("content", "").strip()
                if not answer:
                    answer = "Модель не вернула текстовый ответ. Попробуйте переформулировать вопрос."
                return AgentResult(answer=answer, messages=messages, trace=trace)

            for call in calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if name not in registry:
                    result = {"status": "error", "message": "Инструмент не входит в разрешённый список."}
                else:
                    try:
                        result = registry[name](**arguments)
                        if not isinstance(result, dict):
                            result = {"result": result}
                    except TypeError as exc:
                        result = {"status": "invalid_arguments", "message": str(exc)}
                    except Exception as exc:
                        result = {"status": "tool_error", "message": f"{type(exc).__name__}: {exc}"}
                trace.append({"step": step, "tool": name, "arguments": arguments, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_name": name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

        return AgentResult(
            answer="Достигнут лимит шагов агента. Уточните вопрос или сузьте область поиска.",
            messages=messages,
            trace=trace,
        )
