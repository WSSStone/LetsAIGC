from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from ..errors import PolicyError, ValidationError
from ..paths import local_path
from ..schemas import AgentSession, AgentTaskRecord


class AgentStore:
    """Atomic, local-only persistence for resumable Agent state."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or local_path("agent")).resolve()

    def session_dir(self, session_id: str) -> Path:
        self._safe_id(session_id)
        return self.root / "sessions" / session_id

    def task_dir(self, task_id: str) -> Path:
        self._safe_id(task_id)
        return self.root / "tasks" / task_id

    def save_session(self, session: AgentSession) -> Path:
        path = self.session_dir(session.id) / "session.json"
        self._atomic_write(path, session.model_dump_json(indent=2))
        return path

    def load_session(self, session_id: str) -> AgentSession:
        path = self.session_dir(session_id) / "session.json"
        if not path.is_file():
            raise ValidationError(f"Unknown Agent session: {session_id}")
        return AgentSession.model_validate_json(path.read_text(encoding="utf-8"))

    def save_task(self, task: AgentTaskRecord) -> Path:
        path = self.task_dir(task.id) / "task.json"
        self._atomic_write(path, task.model_dump_json(indent=2))
        return path

    def load_task(self, task_id: str) -> AgentTaskRecord:
        path = self.task_dir(task_id) / "task.json"
        if not path.is_file():
            raise ValidationError(f"Unknown Agent task: {task_id}")
        return AgentTaskRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def inputs_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "inputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def compiled_dir(self, session_id: str, task_id: str) -> Path:
        self._safe_id(task_id)
        path = self.session_dir(session_id) / "tasks" / task_id / "compiled"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @contextmanager
    def execution_lock(self, task_id: str):
        path = self.task_dir(task_id) / "execution.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise PolicyError("Task execution is locked; inspect it before any recovery or retry") from exc
        try:
            os.close(descriptor)
            yield
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _safe_id(value: str) -> None:
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not value or any(char not in allowed for char in value):
            raise ValidationError("Agent identifiers may contain only letters, digits, '-' and '_'")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
