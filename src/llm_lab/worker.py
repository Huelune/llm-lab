"""작업의 RTE 행을 뒤에서 하나씩 LLM에 보내 처리한다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from openai import OpenAI

from llm_lab.fixer import Exchange, FatalLLMError, cache_key, propose_fix
from llm_lab.pages import DECISIONS
from llm_lab.polyspace import Finding
from llm_lab.store import Store
from llm_lab.transcript import render_log, write_log

Submit = Callable[[Callable[[], None]], object]

log = logging.getLogger(__name__)


class Worker:
    """submit은 작업 함수를 받아 실행한다. 서비스에서는 ThreadPoolExecutor.submit,
    테스트에서는 바로 실행하는 함수를 넘긴다. log_dir을 주면 행마다 LLM과의 대화를 남긴다."""

    def __init__(
        self,
        store: Store,
        client: OpenAI,
        model: str,
        submit: Submit,
        log_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.model = model
        self._submit = submit
        self.log_dir = log_dir

    def start(self, job_id: int) -> None:
        """작업의 pending 행을 모두 제출한다."""
        for row in self.store.rows(job_id):
            if row.state == "pending":
                self._submit(lambda row_id=row.id: self._run(job_id, row_id))
        self.store.finish_if_done(job_id)

    def resume(self) -> None:
        """서비스를 켤 때 끝나지 않은 작업을 이어서 처리한다."""
        for job_id in self.store.recover():
            self.start(job_id)

    def _run(self, job_id: int, row_id: int) -> None:
        try:
            self._process(job_id, row_id)
        except Exception:  # 작업자 스레드의 예외는 아무 데도 보이지 않으므로 남긴다
            log.exception("행 %s 처리 중 예상하지 못한 오류", row_id)
            self.store.set_row(row_id, "error", error="예상하지 못한 오류 - 서버 로그 확인")
            self.store.finish_if_done(job_id)

    def _process(self, job_id: int, row_id: int) -> None:
        job = self.store.job(job_id)
        row = self.store.row(row_id)
        if job is None or job.state != "running" or row.state != "pending":
            return  # 작업이 멈췄거나 이미 처리된 행
        assert row.file_name is not None
        self.store.set_row(row_id, "running")
        source = self.store.source(job_id, row.file_name)
        key = cache_key(self.model, source, row.finding)
        exchanges: list[Exchange] = []
        reused = False
        try:
            result = self.store.cache_get(key)
            reused = result is not None
            if result is None:
                result = propose_fix(self.client, self.model, row.finding, source, exchanges)
                self.store.cache_put(key, result)
        except FatalLLMError as exc:
            # 나머지 행도 같은 이유로 실패하므로 작업을 멈춘다. 이 행은 다시 시도 때 처리한다.
            self._save_log(job_id, row.finding, source.name, exchanges, f"작업 중단: {exc}")
            self.store.set_row(row_id, "pending")
            self.store.set_job(job_id, "stopped", str(exc))
            return
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            self._save_log(job_id, row.finding, source.name, exchanges, f"오류: {error}")
            self.store.set_row(row_id, "error", error=error)
        else:
            if reused:
                outcome = "이전 결과 재사용 - LLM을 부르지 않음"
            else:
                outcome = f"{DECISIONS[result.decision]} - {result.reason}"
            self._save_log(job_id, row.finding, source.name, exchanges, outcome)
            self.store.set_row(row_id, "done", result=result)
        self.store.finish_if_done(job_id)

    def _save_log(
        self,
        job_id: int,
        finding: Finding,
        source_name: str,
        exchanges: list[Exchange],
        outcome: str,
    ) -> None:
        """대화 기록 파일을 남긴다. 기록을 못 남겨도 행 처리는 계속한다."""
        if self.log_dir is None:
            return
        text = render_log(
            job_id=job_id,
            finding=finding,
            source_name=source_name,
            model=self.model,
            endpoint=f"{self.client.base_url}chat/completions",
            extra_body=getattr(self.client, "chat_extra_body", {}),
            exchanges=exchanges,
            outcome=outcome,
        )
        try:
            write_log(self.log_dir, job_id, finding, text)
        except OSError:
            log.exception("대화 기록을 남기지 못함: 작업 %s 엑셀 %s행", job_id, finding.row)
