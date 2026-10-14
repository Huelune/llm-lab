"""작업의 행을 묶음으로 모아, 뒤에서 묶음마다 LLM에 보내 처리한다."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from openai import OpenAI

from llm_lab.client import chat_endpoint
from llm_lab.fixer import (
    Ask,
    Exchange,
    FatalLLMError,
    Request,
    cache_key,
    make_request,
    propose_fixes,
    user_error,
)
from llm_lab.followups import Asked, IndexCache, find_asked
from llm_lab.kinds import KINDS, Kind
from llm_lab.pages import DECISIONS
from llm_lab.polyspace import Finding
from llm_lab.sources import DecodeError, SourceFile, decode_source
from llm_lab.store import Row, Store
from llm_lab.transcript import render_log, write_log

Submit = Callable[[Callable[[], None]], object]

log = logging.getLogger(__name__)


def daemon_pool(workers: int) -> Submit:
    """작업 함수를 받아 뒤에서 실행하는 submit.

    ThreadPoolExecutor는 프로그램을 끝낼 때 LLM 응답을 기다리는 작업이 끝나기를 기다리므로
    Ctrl+C가 오래 걸린다. 데몬 스레드는 바로 끝나고, 못 끝낸 행은 다음에 켤 때 이어서 처리한다.
    """
    tasks: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()

    def loop() -> None:
        while True:
            tasks.get()()

    for number in range(workers):
        threading.Thread(target=loop, name=f"fixer-{number}", daemon=True).start()
    return tasks.put


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
        follow_ups: int = 0,
        indexes: IndexCache | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.model = model
        self._submit = submit
        self.log_dir = log_dir
        self.follow_ups = follow_ups  # 묶음마다 정의를 되물을 수 있는 횟수
        self.indexes = indexes or IndexCache()  # 되묻기 때 찾을 작업별 프로젝트 색인

    def start(self, job_id: int) -> None:
        """작업의 pending 행을 묶음으로 모아, 중요한 대상부터 묶음마다 하나씩 제출한다."""
        job = self.store.job(job_id)
        if job is None:
            return
        kind = KINDS[job.kind]
        groups: dict[tuple[str, int], list[Row]] = {}
        for row in self.store.rows(job_id):
            if row.state == "pending":
                # grp가 같은 행은 한 묶음, grp가 없는 행은 혼자 묶음이다
                groups.setdefault((row.grp, 0) if row.grp else ("", row.id), []).append(row)

        def order(rows: list[Row]) -> tuple[int, int]:
            return min(kind.priority(r.finding) for r in rows), min(r.finding.row for r in rows)

        for rows in sorted(groups.values(), key=order):
            row_ids = [row.id for row in rows]
            self._submit(lambda row_ids=row_ids: self._run(job_id, row_ids))
        self.store.finish_if_done(job_id)

    def resume(self) -> None:
        """서비스를 켤 때 끝나지 않은 작업을 이어서 처리한다."""
        for job_id in self.store.recover():
            self.start(job_id)

    def _run(self, job_id: int, row_ids: list[int]) -> None:
        try:
            self._process(job_id, row_ids)
        except Exception:  # 작업자 스레드의 예외는 아무 데도 보이지 않으므로 남긴다
            log.exception("행 %s 처리 중 예상하지 못한 오류", row_ids)
            message = user_error(
                "처리 중 예상하지 못한 문제가 생겼습니다.",
                "서버를 켠 창에 나온 오류 내용을 알려 주세요.",
            )
            for row_id in row_ids:
                self.store.set_row(row_id, "error", error=message)
            self.store.finish_if_done(job_id)

    def _process(self, job_id: int, row_ids: list[int]) -> None:
        job = self.store.job(job_id)
        # 다시 시도를 거듭 누르면 같은 묶음이 두 번 제출되므로, 먼저 가져간 작업자만 처리한다
        if job is None or job.state != "running" or not self.store.claim_rows(row_ids):
            return  # 작업이 멈췄거나 이미 처리 중이거나 끝난 묶음
        kind = KINDS[job.kind]
        rows = sorted((self.store.row(row_id) for row_id in row_ids), key=_order_in_group)
        findings = [row.finding for row in rows]
        assert rows[0].file_name is not None
        source = self.store.source(job_id, rows[0].file_name)
        context = json.loads(job.context) if job.context else None
        exchanges: list[Exchange] = []
        rounds: list[Asked] = []
        reused = False
        try:
            request = make_request(
                kind,
                findings,
                source,
                context,
                self.store.definitions(job_id),
                self.store.sources(job_id),
                follow_ups=self.follow_ups,
            )
            key = cache_key(self.model, request)
            entry = self.store.cache_entry(key)
            results = None
            if entry is not None and self._restore(job_id, job.source_root, entry[1]):
                results = entry[0]
            reused = results is not None
            if results is None:
                used: dict[str, str] = {}  # 되묻기로 고칠 수 있게 보낸 파일 → sha256
                ask = self._asker(job_id, job.source_root, rounds, used)
                results = propose_fixes(self.client, self.model, request, exchanges, ask=ask)
                self.store.cache_put(key, results, files=sorted(used.items()))
        except FatalLLMError as exc:
            # 나머지 묶음도 같은 이유로 실패하므로 작업을 멈춘다. 이 묶음은 다시 시도 때 처리한다.
            self._save_log(job_id, kind, findings, source.name, exchanges, [f"작업 중단: {exc}"])
            for row in rows:
                self.store.set_row(row.id, "pending")
            self.store.set_job(job_id, "stopped", str(exc))
            return
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            # 기록 파일의 "결과" 줄은 한 줄이어야 목록이 깨지지 않는다
            outcome = "오류: " + " / ".join(error.splitlines())
            self._save_log(job_id, kind, findings, source.name, exchanges, [outcome])
            for row in rows:
                self.store.set_row(row.id, "error", error=error)
        else:
            if reused:
                outcomes = ["이전 결과 재사용 - LLM을 부르지 않음"]
            else:
                outcomes = [f"{DECISIONS[result.decision]} - {result.reason}" for result in results]
                if len(rows) > 1:
                    outcomes = [
                        f"엑셀 {row.finding.row}행 {outcome}"
                        for row, outcome in zip(rows, outcomes, strict=True)
                    ]
            if rounds:
                outcomes = [f"정의 되묻기 {len(rounds)}번", *outcomes]
            self._save_log(job_id, kind, findings, source.name, exchanges, outcomes)
            for row, result in zip(rows, results, strict=True):
                self.store.set_row(row.id, "done", result=result)
        self.store.finish_if_done(job_id)

    def _asker(self, job_id: int, root: str, rounds: list[Asked], used: dict[str, str]) -> Ask:
        """되물은 이름을 작업의 프로젝트 색인에서 찾는 함수.

        보낸 정의는 작업에 더한다. 고칠 수 있는 종류면 그 정의의 파일도 작업에 더하고(작업에 없으면
        소스 폴더에서 다시 읽는다), 다시 읽지 못한 파일의 정의는 참고만으로 보낸다.
        """

        def ask(names: list[str], request: Request) -> Asked:
            index = self.indexes.get(job_id, root)
            asked = find_asked(
                names,
                index,
                request.source.name,
                request.first,
                request.last,
                (*request.definitions, *request.asked),
            )
            files: dict[str, SourceFile] = {}
            if request.kind.edits_definitions and index is not None:
                stored = self.store.sources(job_id)
                for name in dict.fromkeys(d.file for d in asked.definitions):
                    if name in request.files:
                        continue
                    if name == request.source.name:
                        files[name] = request.source
                        continue
                    found = stored.get(name) or index.source(name)
                    if found is not None:
                        files[name] = found
                        used[name] = found.sha256
                self.store.add_files(job_id, files.values())
            self.store.add_definitions(job_id, asked.definitions)
            asked = replace(asked, files=files)
            rounds.append(asked)
            return asked

        return ask

    def _restore(self, job_id: int, root: str, files: list[tuple[str, str]]) -> bool:
        """저장된 답이 기댄 파일을 작업에 둔다. 그 파일이 그때와 다르거나 없으면 False."""
        stored = self.store.sources(job_id)
        missing: list[SourceFile] = []
        for name, sha256 in files:
            have = stored.get(name)
            if have is None and root:
                try:
                    have = decode_source(name, (Path(root) / name).read_bytes())
                except DecodeError, OSError:
                    have = None
                if have is not None:
                    missing.append(have)
            if have is None or have.sha256 != sha256:
                return False
        self.store.add_files(job_id, missing)
        return True

    def _save_log(
        self,
        job_id: int,
        kind: Kind,
        findings: list[Finding],
        source_name: str,
        exchanges: list[Exchange],
        outcomes: list[str],
    ) -> None:
        """대화 기록 파일을 남긴다. 기록을 못 남겨도 묶음 처리는 계속한다."""
        if self.log_dir is None:
            return
        text = render_log(
            job_id=job_id,
            kind=kind.title,
            findings=findings,
            source_name=source_name,
            model=self.model,
            endpoint=chat_endpoint(self.client, self.model),
            extra_body=getattr(self.client, "chat_extra_body", {}),
            exchanges=exchanges,
            outcomes=outcomes,
        )
        first_row = min(finding.row for finding in findings)
        try:
            write_log(self.log_dir, job_id, first_row, text)
        except OSError:
            log.exception("대화 기록을 남기지 못함: 작업 %s 엑셀 %s행", job_id, first_row)


def _order_in_group(row: Row) -> tuple[int, str, str, int]:
    """묶음 안의 순서 = LLM 메시지의 Finding 번호 순서. 엑셀 행은 마지막 기준이라
    엑셀을 다시 뽑아 행 번호가 바뀌어도 같은 지적이면 순서와 결과 재사용 키가 그대로다."""
    finding = row.finding
    return finding.line or 0, finding.check, finding.detail, finding.row
