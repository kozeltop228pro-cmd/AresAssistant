from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

APPDATA_ROOT = Path(os.environ.get("APPDATA", Path.home())) / "AresAssistant"
APPDATA_ROOT.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APPDATA_ROOT / "config.json"
LOG_DIR = APPDATA_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APPDATA_ROOT / "knowledge.db"
SESSION_PATH = APPDATA_ROOT / "telegram_session"

DEFAULTS: dict[str, Any] = {
    "repunish_schedule": "manual",
    "repunish_daily_time": "12:00",
    "repunish_delay_seconds": 2,
    "repunish_notify_start": True,

    "mode": "draft",
    "learning_enabled": True,
    "learning_noise_filter": True,
    "learning_only_corrections": True,
    "learning_policy": "unknown_or_corrections",
    "learning_exclusions": [
        "проверка связи бота",
        "служебное сообщение",
    ],
    "auto_close_enabled": True,
    "global_close_phrases": [],
    "close_command_template": "t c {ticket_id}",
    "monitoring": False,
    # Свои API ID и API Hash берутся на my.telegram.org и вводятся в «Настройках».
    "api_id": "",
    "api_hash": "",
    "auth_method": "qr",
    "phone": "",
    "source_chat": "@AresMineRobot",
    "response_chat": "@AresMineRobot",
    "command_relay_enabled": True,
    "command_relay_user_id": "123456789",
    "command_relay_timeout_seconds": 15,
    "command_relay_forward": False,
    "command_relay_needs_check": False,
    "draft_threshold": 80,
    "auto_threshold": 96,
    "auto_delay_seconds": 1.5,
    "auto_limit_per_minute": 6,
    # 2.9.6 uses the deterministic semantic-context engine; Ollama is kept only for backward-compatible config import.
    "local_ai_enabled": False,
    "context_engine_version": 2,
    "local_ai_endpoint": "http://127.0.0.1:11434",
    "local_ai_model": "qwen3.5:4b",
    "local_ai_draft_threshold": 86,
    "local_ai_auto_threshold": 94,
    "local_ai_candidate_limit": 6,
    "local_ai_timeout_seconds": 5,
    "local_ai_context_size": 2048,
    "local_ai_num_predict": 48,
    "local_ai_keep_alive": "60m",
    "local_ai_prewarm": True,
    "local_ai_fast_path_confidence": 82,
    "local_ai_timeout_fallback_confidence": 80,
    "local_ai_speed_profile_version": 2,
    "local_ai_safe_fallback": True,
    "voice_enabled": True,
    "startup_voice": True,
    "startup_phrase": "Алексей, приветствую!",
    "voice_wake_word_required": False,
    "microphone_token_id": "",
    "open_telegram_on_draft": True,
    "start_with_windows": True,
    "minimize_to_tray": True,
    "last_window_geometry": "1440x900",
    "last_seen_changelog_version": "",
}

_SENSITIVE_FIELDS = {"api_hash", "phone"}


def _protect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("dpapi:"):
        return value
    try:
        import win32crypt  # type: ignore

        blob = win32crypt.CryptProtectData(value.encode("utf-8"), "AresAssistant", None, None, None, 0)
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    except Exception:
        return value


def _unprotect(value: str) -> str:
    if not value.startswith("dpapi:"):
        return value
    try:
        import win32crypt  # type: ignore

        raw = base64.b64decode(value[6:])
        plain = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
        return plain.decode("utf-8")
    except Exception:
        return ""


def load_config() -> dict[str, Any]:
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
                # 2.9.6 disables Ollama as a decision maker. Semantic routing is local and
                # uses action phrases, the whole ticket and confirmed examples.
                if int(loaded.get("context_engine_version", 0) or 0) < 2:
                    data["local_ai_enabled"] = False
                    data["context_engine_version"] = 2
                # 2.9.3 keeps confirmed database answers independent from Ollama timeouts and
                # tightens the second-stage prompt for genuinely unknown cases.
                if int(loaded.get("local_ai_speed_profile_version", 0) or 0) < 2:
                    data.update(
                        {
                            "local_ai_candidate_limit": 6,
                            "local_ai_timeout_seconds": 5,
                            "local_ai_context_size": 2048,
                            "local_ai_num_predict": 48,
                            "local_ai_keep_alive": "60m",
                            "local_ai_prewarm": True,
                            "local_ai_fast_path_confidence": 82,
                            "local_ai_timeout_fallback_confidence": 80,
                            "local_ai_speed_profile_version": 2,
                        }
                    )
        except Exception:
            pass
    for field in _SENSITIVE_FIELDS:
        data[field] = _unprotect(str(data.get(field, "")))
    return data


def save_config(config: dict[str, Any]) -> None:
    stored = dict(config)
    for field in _SENSITIVE_FIELDS:
        stored[field] = _protect(str(stored.get(field, "")))
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CONFIG_PATH)
