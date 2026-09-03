from __future__ import annotations

from pathlib import Path

import pytest

from letsaigc.agent.responses import (
    ResponsesAgentModel,
    build_llm_client,
    endpoint_fingerprint,
    llm_base_url,
    load_llm_api_key,
)
from letsaigc.config import get_int_setting, get_setting, get_url_setting
from letsaigc.errors import ReadinessError, ValidationError


@pytest.fixture
def repo(monkeypatch, tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setenv("LETSAIGC_ROOT", str(tmp_path))
    return tmp_path


def test_process_environment_takes_priority_over_dotenv(repo, monkeypatch) -> None:
    (repo / ".env").write_text("LLM_DECISION_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("LLM_DECISION_MODEL", "from-process")
    assert get_setting("LLM_DECISION_MODEL") == "from-process"


def test_dotenv_is_used_when_process_environment_is_absent(repo, monkeypatch) -> None:
    (repo / ".env").write_text('LLM_API_KEY="dotenv-secret"\n', encoding="utf-8")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert get_setting("LLM_API_KEY") == "dotenv-secret"


def test_empty_values_are_treated_as_unset(repo, monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "   ")
    assert get_setting("LLM_API_KEY") is None
    assert get_setting("LLM_API_KEY", "fallback") == "fallback"


def test_int_setting_rejects_non_integer(repo, monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_PORT", "not-a-number")
    with pytest.raises(ValidationError):
        get_int_setting("MLFLOW_PORT", 5000)
    monkeypatch.delenv("MLFLOW_PORT", raising=False)
    assert get_int_setting("MLFLOW_PORT", 5000) == 5000


def test_url_setting_rejects_values_without_scheme(repo, monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "relay.example.com")
    with pytest.raises(ValidationError):
        get_url_setting("LLM_BASE_URL")
    monkeypatch.setenv("LLM_BASE_URL", "https://relay.example.com/v1")
    assert get_url_setting("LLM_BASE_URL") == "https://relay.example.com/v1"


def test_legacy_variable_names_no_longer_resolve(repo, monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-secret")
    monkeypatch.setenv("LETSAIGC_AGENT_MODEL", "legacy-model")
    assert load_llm_api_key() is None
    assert ResponsesAgentModel().decision_model == "gpt-5.6-luna"


def test_build_llm_client_injects_key_and_base_url(repo, monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "unit-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://relay.example.com/v1")
    client = build_llm_client()
    assert client.api_key == "unit-key"
    assert str(client.base_url).rstrip("/") == "https://relay.example.com/v1"
    assert llm_base_url() == "https://relay.example.com/v1"
    assert endpoint_fingerprint() is not None


def test_build_llm_client_requires_key(repo, monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ReadinessError):
        build_llm_client()
