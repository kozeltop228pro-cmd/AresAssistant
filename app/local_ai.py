from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(slots=True)
class AIResult:
    decision: str
    template_id: Optional[int]
    confidence: int
    reason: str
    available: bool = True
    error: str = ""
    elapsed_ms: int = 0
    load_ms: int = 0


@dataclass(slots=True)
class AIStatus:
    available: bool
    model_installed: bool
    endpoint: str
    model: str
    version: str = ""
    error: str = ""


class LocalAIAnalyzer:
    """Fast, strict local semantic reranker for Ares Assistant.

    The model never writes a player-facing reply. It can only pick one approved
    template ID or return UNKNOWN. Ollama is used only as the second-stage judge;
    deterministic/exact and high-confidence local matches can bypass it.
    """

    def __init__(self, config: dict[str, Any]):
        self.update_config(config)

    def update_config(self, config: dict[str, Any]) -> None:
        self.config = config
        self.endpoint = str(config.get("local_ai_endpoint", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").rstrip("/")
        self.model = str(config.get("local_ai_model", "qwen3.5:4b") or "qwen3.5:4b").strip()
        # Ticket inference must stay quick. Long model loading is handled by background prewarm.
        self.timeout = max(3.0, min(20.0, float(config.get("local_ai_timeout_seconds", 5) or 5)))
        self.keep_alive = config.get("local_ai_keep_alive", "60m") or "60m"
        self.context_size = max(2048, min(8192, int(config.get("local_ai_context_size", 2048) or 2048)))
        self.num_predict = max(48, min(140, int(config.get("local_ai_num_predict", 48) or 48)))

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"

    def _request_json(
        self,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        method: str = "POST",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        url = self.endpoint + path
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}

    def status(self) -> AIStatus:
        try:
            version_data: dict[str, Any] = {}
            try:
                version_data = self._request_json("/api/version", None, "GET", timeout=3.0)
            except Exception:
                version_data = {}
            tags = self._request_json("/api/tags", None, "GET", timeout=3.0)
            names: set[str] = set()
            for item in tags.get("models", []) or []:
                if not isinstance(item, dict):
                    continue
                for key in ("name", "model"):
                    value = str(item.get(key, "") or "").strip()
                    if value:
                        names.add(value)
            model_installed = self.model in names or any(name.split(":", 1)[0] == self.model for name in names if ":" not in self.model)
            return AIStatus(
                available=True,
                model_installed=model_installed,
                endpoint=self.endpoint,
                model=self.model,
                version=str(version_data.get("version", "") or ""),
            )
        except Exception as exc:
            return AIStatus(False, False, self.endpoint, self.model, error=self._friendly_error(exc))

    def prewarm(self) -> tuple[bool, str, int]:
        """Load the configured model in the background and keep it resident.

        This request is intentionally allowed to take longer because it never blocks a
        ticket. Later ticket checks use the much shorter inference timeout.
        """
        started = time.perf_counter()
        try:
            payload = {
                "model": self.model,
                "prompt": "",
                "stream": False,
                "keep_alive": self.keep_alive,
                "options": {"num_ctx": min(self.context_size, 4096), "num_predict": 1},
            }
            response = self._request_json("/api/generate", payload, "POST", timeout=120.0)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            load_ms = int(int(response.get("load_duration", 0) or 0) / 1_000_000)
            return True, "Модель прогрета и готова", max(elapsed_ms, load_ms)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return False, self._friendly_error(exc), elapsed_ms

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        if isinstance(exc, urllib.error.URLError):
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                return "Ollama не ответил за короткий лимит времени"
            return f"Ollama недоступен: {reason}"
        if isinstance(exc, TimeoutError):
            return "Ollama не ответил за короткий лимит времени"
        text = str(exc) or exc.__class__.__name__
        if "timed out" in text.lower():
            return "Ollama не ответил за короткий лимит времени"
        return text

    @staticmethod
    def find_ollama_executable() -> Optional[str]:
        found = shutil.which("ollama") or shutil.which("ollama.exe")
        if found:
            return found
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates = [
            local / "Programs" / "Ollama" / "ollama.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def start_pull_console(self) -> tuple[bool, str]:
        executable = self.find_ollama_executable()
        if not executable:
            return False, "Ollama не найден. Сначала установи Ollama для Windows."
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen([executable, "pull", self.model], creationflags=creationflags)
            return True, f"Открыта загрузка модели {self.model}"
        except Exception as exc:
            return False, str(exc)

    def analyze(self, question: str, candidates: Iterable[dict[str, Any]]) -> AIResult:
        started = time.perf_counter()
        candidate_rows: list[dict[str, Any]] = []
        valid_ids: set[int] = set()
        # The caller already ranks candidates. Keep the prompt compact: this matters much
        # more for latency than a huge context window, while still giving the model several
        # competing meanings to compare.
        max_rows = max(4, min(16, int(self.config.get("local_ai_candidate_limit", 6) or 6)))
        for candidate in candidates:
            if len(candidate_rows) >= max_rows:
                break
            try:
                template_id = int(candidate.get("id"))
            except Exception:
                continue
            valid_ids.add(template_id)
            candidate_rows.append(
                {
                    "id": template_id,
                    "name": self._clip(str(candidate.get("name", "")), 90),
                    "answer": self._clip(str(candidate.get("answer", "")), 180),
                    "triggers": [self._clip(str(item), 70) for item in (candidate.get("triggers", []) or [])[:6]],
                    "negative": [self._clip(str(item), 70) for item in (candidate.get("negative_triggers", []) or [])[:5]],
                    "examples": [self._clip(str(item), 150) for item in (candidate.get("examples", []) or [])[:2]],
                }
            )

        if not candidate_rows:
            return AIResult("unknown", None, 0, "Нет разрешённых шаблонов для смысловой проверки")

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["use", "unknown"]},
                "template_id": {"type": ["integer", "null"]},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "reason": {"type": "string"},
            },
            "required": ["decision", "template_id", "confidence", "reason"],
            "additionalProperties": False,
        }

        system_prompt = (
            "Ты классификатор тикетов AresMine. Не отвечай игроку и не придумывай текст. "
            "Выбери только ID подходящего шаблона или unknown. Смотри на ВЕСЬ смысл: что произошло, "
            "чего хочет игрок и какое действие требуется. Одно совпавшее слово ничего не доказывает. "
            "'аккаунт забанили' != 'потерял доступ к аккаунту'; 'банковская' != 'бан'; случайное 'хелпер' "
            "не означает тему хелперов. Если шаблон отвечает на соседнюю тему, есть два равных варианта "
            "или данных мало — unknown. confidence 95+ только когда смысл практически однозначен. "
            "reason: максимум 12 слов, без рассуждений."
        )
        user_payload = {
            "ticket": self._clip(question, 2600),
            "templates": candidate_rows,
        }
        request_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "format": schema,
            "options": {
                "temperature": 0,
                "num_ctx": self.context_size,
                "num_predict": self.num_predict,
                "top_k": 10,
                "top_p": 0.4,
            },
        }

        try:
            response = self._request_json("/api/chat", request_payload, "POST")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            load_ms = int(int(response.get("load_duration", 0) or 0) / 1_000_000)
            message = response.get("message") or {}
            content = str(message.get("content", "") or "").strip()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("ИИ вернул ответ неправильного формата")
            decision = str(result.get("decision", "unknown") or "unknown").lower()
            raw_id = result.get("template_id")
            template_id = int(raw_id) if raw_id is not None else None
            confidence = max(0, min(100, int(result.get("confidence", 0) or 0)))
            reason = self._clip(str(result.get("reason", "") or ""), 180) or "Смысловая проверка завершена"
            if decision != "use" or template_id not in valid_ids:
                return AIResult("unknown", None, min(confidence, 89), reason, elapsed_ms=elapsed_ms, load_ms=load_ms)
            return AIResult("use", template_id, confidence, reason, elapsed_ms=elapsed_ms, load_ms=load_ms)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return AIResult(
                "error",
                None,
                0,
                "Локальный ИИ не успел подтвердить ответ",
                available=False,
                error=self._friendly_error(exc),
                elapsed_ms=elapsed_ms,
            )
