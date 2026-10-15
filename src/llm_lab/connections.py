"""LLM 연결 값과 연결 목록 JSON 저장소."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from llm_lab.config import (
    DEFAULT_PROJECT_HEADER,
    DEFAULT_TIMEOUT,
    ConfigError,
    Settings,
    settings_from_env,
)

ENV_NAME = ".env"
_WRITE_FAILED = "연결 설정을 저장하지 못했습니다. 데이터 폴더에 쓸 수 있는지 확인하세요."
_ENV_READ_ONLY = ".env 연결은 .env 파일에서 고칩니다"


@dataclass(frozen=True)
class Connection:
    name: str
    client_kind: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    project_id: str = field(default="", repr=False)
    project_header: str = DEFAULT_PROJECT_HEADER
    api_version: str = ""
    timeout: float = DEFAULT_TIMEOUT
    verify_ssl: bool = True
    enable_thinking: bool | None = None
    read_only: bool = False

    def settings(self, base: Settings) -> Settings:
        """base의 workers·follow_ups에 이 연결의 값을 얹은 Settings."""
        return replace(
            base,
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout=self.timeout,
            verify_ssl=self.verify_ssl,
            enable_thinking=self.enable_thinking,
            project_id=self.project_id,
            project_header=self.project_header,
            client_kind=self.client_kind,
            api_version=self.api_version,
        )

    def fingerprint(self) -> str:
        """모든 값의 sha256. 값이 바뀌면 클라이언트 캐시를 버리는 데 쓴다."""
        text = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connection_from_settings(name: str, settings: Settings, read_only: bool) -> Connection:
    return Connection(
        name=name,
        client_kind=settings.client_kind,
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        project_id=settings.project_id,
        project_header=settings.project_header,
        api_version=settings.api_version,
        timeout=settings.timeout,
        verify_ssl=settings.verify_ssl,
        enable_thinking=settings.enable_thinking,
        read_only=read_only,
    )


def connection_from_form(form: Mapping[str, str], keep: Connection | None = None) -> Connection:
    """폼 값을 settings_from_env와 같은 규칙으로 검사해 Connection으로 만든다.

    api_key·project_id가 비어 있고 keep이 있으면 keep의 값을 쓴다. 단 주소나 접속 방식이
    바뀌었으면 저장된 키·프로젝트 ID를 새 곳으로 보내지 않는다 (키가 비어 있으면 다시 묻는다).
    """
    name = form.get("name", "").strip()
    if not name:
        raise ConfigError("연결 이름을 적으세요")
    if keep is not None:
        # 비교는 저장된 모양(settings_from_env 규칙)으로 한다. 주소·방식만 먼저 정리해 얻는다
        target = settings_from_env(
            {
                "LLM_BASE_URL": form.get("base_url", ""),
                "LLM_CLIENT": form.get("client_kind", ""),
                "LLM_API_KEY": form.get("api_key", "") or keep.api_key,
            },
            require_connection=False,
        )
        if (target.base_url, target.client_kind) != (keep.base_url, keep.client_kind):
            if not form.get("api_key", "").strip() and keep.api_key:
                raise ConfigError("주소나 접속 방식을 바꾸면 키를 다시 입력하세요.")
            keep = None
    settings = settings_from_env(
        {
            "LLM_BASE_URL": form.get("base_url", ""),
            "LLM_MODEL": form.get("model", ""),
            "LLM_API_KEY": form.get("api_key", "") or (keep.api_key if keep else ""),
            "LLM_PROJECT_ID": form.get("project_id", "") or (keep.project_id if keep else ""),
            "LLM_PROJECT_HEADER": form.get("project_header", ""),
            "LLM_TIMEOUT": form.get("timeout", ""),
            "LLM_VERIFY_SSL": form.get("verify_ssl", ""),
            "LLM_ENABLE_THINKING": form.get("enable_thinking", ""),
            "LLM_CLIENT": form.get("client_kind", ""),
            "LLM_API_VERSION": form.get("api_version", ""),
        }
    )
    return connection_from_settings(name, settings, read_only=False)


_JSON_KEYS = {f.name for f in fields(Connection)} - {"read_only"}


def _to_json(connection: Connection) -> dict:
    return {k: v for k, v in asdict(connection).items() if k in _JSON_KEYS}


def _from_json(data: dict) -> Connection:
    return Connection(**{k: v for k, v in data.items() if k in _JSON_KEYS})


class ConnectionStore:
    """연결 목록. env 연결(있으면)이 맨 앞에 오고 파일에는 쓰지 않는다."""

    def __init__(self, path: Path, env: Connection | None = None) -> None:
        self._path = path
        self._env = env
        self._lock = threading.Lock()
        self._saved: list[Connection] = []
        self._current_name = ""
        self.error = ""
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._saved = [_from_json(item) for item in data.get("connections", [])]
            self._current_name = str(data.get("current", ""))
        except Exception as exc:  # 종류만 남긴다 (메시지에 경로·값이 있을 수 있음)
            self._saved = []
            self._current_name = ""
            self.error = type(exc).__name__

    def _commit(self, saved: list[Connection], current_name: str) -> None:
        """새 상태를 파일에 먼저 쓰고, 성공하면 메모리에 반영한다. 실패는 안내만 남긴다."""
        data = {"current": current_name, "connections": [_to_json(c) for c in saved]}
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            # 키가 들어 있으므로 만들 때부터 소유자만 읽게 한다 (Windows에서는 mode가 대부분 무시됨)
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise ConfigError(_WRITE_FAILED) from None
        self._saved = saved
        self._current_name = current_name

    def _all(self) -> list[Connection]:
        return ([self._env] if self._env else []) + self._saved

    def _find(self, name: str) -> Connection | None:
        return next((c for c in self._all() if c.name == name), None)

    def _current(self) -> Connection | None:
        return self._find(self._current_name) or next(iter(self._all()), None)

    def list(self) -> list[Connection]:
        with self._lock:
            return self._all()

    def get(self, name: str) -> Connection | None:
        with self._lock:
            return self._find(name)

    def current(self) -> Connection | None:
        with self._lock:
            return self._current()

    def save(self, connection: Connection, original: str | None = None) -> None:
        """추가(original이 None) 또는 수정. 이름이 바뀐 current는 current도 따라간다."""
        with self._lock:
            if self._env and ENV_NAME in (original, connection.name):
                raise ConfigError(_ENV_READ_ONLY)
            if any(c.name == connection.name and c.name != original for c in self._all()):
                raise ConfigError("같은 이름의 연결이 있습니다")
            saved = list(self._saved)
            current_name = self._current_name
            if original is None:
                saved.append(connection)
            else:
                index = next((i for i, c in enumerate(saved) if c.name == original), None)
                if index is None:
                    raise ConfigError("없는 연결입니다")
                if self._current() is saved[index]:
                    current_name = connection.name
                saved[index] = connection
            self._commit(saved, current_name)

    def delete(self, name: str) -> None:
        with self._lock:
            target = self._find(name)
            if target is None:
                raise ConfigError("없는 연결입니다")
            if target.read_only:
                raise ConfigError(_ENV_READ_ONLY)
            if target is self._current():
                raise ConfigError("현재 연결은 지울 수 없습니다. 다른 연결을 먼저 고르세요")
            self._commit([c for c in self._saved if c.name != name], self._current_name)

    def select(self, name: str) -> None:
        with self._lock:
            if self._find(name) is None:
                raise ConfigError("없는 연결입니다")
            self._commit(self._saved, name)
