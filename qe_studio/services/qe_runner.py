from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import queue
import os
import signal
import selectors
import codecs
import subprocess
import threading
import uuid
from typing import Callable, MutableMapping, Sequence


RUN_ACTIVE_STATE_KEY = "run_active"
RUN_FINISHED_STATE_KEY = "run_finished"
RUN_RETURNCODE_STATE_KEY = "run_returncode"
RUN_TAIL_LINES_STATE_KEY = "tail_lines"
RUN_OUTPUT_PATH_STATE_KEY = "output_path"
RUN_OUTPUT_PATHS_STATE_KEY = "output_paths"
ACTIVE_RUN_ID_STATE_KEY = "active_run_id"
RUN_JOB_LABEL_STATE_KEY = "run_job_label"
RUN_JOB_OWNER_STATE_KEY = "run_job_owner"
RUN_PHASE_LABEL_STATE_KEY = "run_phase_label"
RUN_STATE_KEYS = (
    RUN_ACTIVE_STATE_KEY,
    RUN_FINISHED_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    RUN_OUTPUT_PATH_STATE_KEY,
    RUN_OUTPUT_PATHS_STATE_KEY,
    ACTIVE_RUN_ID_STATE_KEY,
    RUN_JOB_LABEL_STATE_KEY,
    RUN_JOB_OWNER_STATE_KEY,
    RUN_PHASE_LABEL_STATE_KEY,
)

EVENT_TYPE_LINE = "line"
EVENT_TYPE_PHASE = "phase"
EVENT_TYPE_COMPLETE = "complete"
DEFAULT_FAILURE_RETURNCODE = -1
DEFAULT_MAX_TAIL_LINES = 200
RUNTIME_LOST_MESSAGE = (
    "Live Quantum ESPRESSO runtime state was lost. "
    "The app can no longer stream this job."
)


@dataclass(frozen=True)
class RunEvent:
    type: str
    line: str | None = None
    returncode: int | None = None
    phase_label: str | None = None
    output_path: str | None = None


@dataclass(frozen=True)
class JobStep:
    label: str
    command: Sequence[str]
    cwd: Path
    output_path: Path
    prepare: Callable[[], None] | None = None
    validator: Callable[[], None] | None = None


@dataclass
class RunContext:
    process: subprocess.Popen[str] | None
    thread: threading.Thread
    queue: queue.Queue[RunEvent]
    started_at: datetime


RUN_REGISTRY: dict[str, RunContext] = {}


def initialize_run_state(state: MutableMapping[str, object]) -> None:
    """Ensure all run-related session state keys exist."""
    state.setdefault(RUN_ACTIVE_STATE_KEY, False)
    state.setdefault(RUN_FINISHED_STATE_KEY, False)
    state.setdefault(RUN_RETURNCODE_STATE_KEY, None)
    state.setdefault(RUN_TAIL_LINES_STATE_KEY, [])
    state.setdefault(RUN_OUTPUT_PATH_STATE_KEY, None)
    state.setdefault(RUN_OUTPUT_PATHS_STATE_KEY, {})
    state.setdefault(ACTIVE_RUN_ID_STATE_KEY, None)
    state.setdefault(RUN_JOB_LABEL_STATE_KEY, None)
    state.setdefault(RUN_JOB_OWNER_STATE_KEY, None)
    state.setdefault(RUN_PHASE_LABEL_STATE_KEY, None)


def cleanup_run(run_id: str | None) -> None:
    """Remove a run context from the module-level registry."""
    if not run_id:
        return
    RUN_REGISTRY.pop(run_id, None)


def cleanup_finished_run(run_id: str | None) -> None:
    """Remove a run context if its worker thread has already stopped."""
    if not run_id:
        return
    context = RUN_REGISTRY.get(run_id)
    if context is not None and not context.thread.is_alive():
        cleanup_run(run_id)


def reset_run_state(state: MutableMapping[str, object]) -> None:
    """Reset durable run state and clean up any finished registry entry."""
    initialize_run_state(state)
    cleanup_finished_run(state.get(ACTIVE_RUN_ID_STATE_KEY))
    state[RUN_ACTIVE_STATE_KEY] = False
    state[RUN_FINISHED_STATE_KEY] = False
    state[RUN_RETURNCODE_STATE_KEY] = None
    state[RUN_TAIL_LINES_STATE_KEY] = []
    state[RUN_OUTPUT_PATH_STATE_KEY] = None
    state[RUN_OUTPUT_PATHS_STATE_KEY] = {}
    state[ACTIVE_RUN_ID_STATE_KEY] = None
    state[RUN_JOB_LABEL_STATE_KEY] = None
    state[RUN_JOB_OWNER_STATE_KEY] = None
    state[RUN_PHASE_LABEL_STATE_KEY] = None


def set_run_started(
    state: MutableMapping[str, object],
    *,
    run_id: str,
    output_path: Path,
    output_paths: dict[str, str] | None = None,
    job_label: str | None = None,
    job_owner: str | None = None,
    phase_label: str | None = None,
) -> None:
    """Store a fresh running-state snapshot in session state."""
    initialize_run_state(state)
    state[RUN_ACTIVE_STATE_KEY] = True
    state[RUN_FINISHED_STATE_KEY] = False
    state[RUN_RETURNCODE_STATE_KEY] = None
    state[RUN_TAIL_LINES_STATE_KEY] = []
    state[RUN_OUTPUT_PATH_STATE_KEY] = str(output_path)
    state[RUN_OUTPUT_PATHS_STATE_KEY] = dict(output_paths or {})
    state[ACTIVE_RUN_ID_STATE_KEY] = run_id
    state[RUN_JOB_LABEL_STATE_KEY] = job_label
    state[RUN_JOB_OWNER_STATE_KEY] = job_owner
    state[RUN_PHASE_LABEL_STATE_KEY] = phase_label


def append_tail_line(
    state: MutableMapping[str, object],
    line: str,
    *,
    max_tail_lines: int = DEFAULT_MAX_TAIL_LINES,
) -> None:
    """Append one line and clamp the in-memory tail window."""
    existing_lines = list(state.get(RUN_TAIL_LINES_STATE_KEY, []))
    existing_lines.append(line)
    state[RUN_TAIL_LINES_STATE_KEY] = existing_lines[-max_tail_lines:]


def apply_run_events(
    state: MutableMapping[str, object],
    events: Sequence[RunEvent],
    *,
    max_tail_lines: int = DEFAULT_MAX_TAIL_LINES,
) -> bool:
    """Apply queued worker events to session state."""
    initialize_run_state(state)
    run_completed = False

    for event in events:
        if event.type == EVENT_TYPE_PHASE:
            state[RUN_PHASE_LABEL_STATE_KEY] = event.phase_label
            if event.output_path is not None:
                state[RUN_OUTPUT_PATH_STATE_KEY] = event.output_path
            if event.line is not None:
                append_tail_line(state, event.line, max_tail_lines=max_tail_lines)
        elif event.type == EVENT_TYPE_LINE and event.line is not None:
            append_tail_line(state, event.line, max_tail_lines=max_tail_lines)
        elif event.type == EVENT_TYPE_COMPLETE:
            state[RUN_ACTIVE_STATE_KEY] = False
            state[RUN_FINISHED_STATE_KEY] = True
            state[RUN_RETURNCODE_STATE_KEY] = (
                DEFAULT_FAILURE_RETURNCODE if event.returncode is None else event.returncode
            )
            run_completed = True

    return run_completed


def mark_runtime_lost(state: MutableMapping[str, object]) -> None:
    """Record that the live registry entry disappeared across reruns."""
    initialize_run_state(state)
    append_tail_line(state, f"{RUNTIME_LOST_MESSAGE}\n")
    state[RUN_ACTIVE_STATE_KEY] = False
    state[RUN_FINISHED_STATE_KEY] = True
    state[RUN_RETURNCODE_STATE_KEY] = DEFAULT_FAILURE_RETURNCODE


def reconcile_run_state(state: MutableMapping[str, object], *, max_tail_lines: int = DEFAULT_MAX_TAIL_LINES) -> bool:
    """Sync stored run state with the live registry on non-streaming pages.

    Returns ``True`` when the session state changed. This helper mirrors the
    recovery logic used by the live Run page so pages like Setup do not keep a
    stale ``run_active`` lock after a finished or lost background job.
    """
    initialize_run_state(state)
    if not state.get(RUN_ACTIVE_STATE_KEY):
        return False

    active_run_id = state.get(ACTIVE_RUN_ID_STATE_KEY)
    if not active_run_id:
        mark_runtime_lost(state)
        return True

    context = get_run_context(str(active_run_id))
    if context is None:
        mark_runtime_lost(state)
        return True

    events = drain_run_events(str(active_run_id))
    run_completed = apply_run_events(
        state,
        events,
        max_tail_lines=max_tail_lines,
    )
    if run_completed:
        cleanup_run(str(active_run_id))
        return True

    if state.get(RUN_ACTIVE_STATE_KEY) and not context.thread.is_alive():
        mark_runtime_lost(state)
        cleanup_run(str(active_run_id))
        return True

    return bool(events)


def get_run_context(run_id: str | None) -> RunContext | None:
    """Return the live run context for the given id, if present."""
    if not run_id:
        return None
    return RUN_REGISTRY.get(run_id)


def drain_run_events(run_id: str | None) -> list[RunEvent]:
    """Drain all currently available queue events for a live run."""
    context = get_run_context(run_id)
    if context is None:
        return []

    events: list[RunEvent] = []
    while True:
        try:
            events.append(context.queue.get_nowait())
        except queue.Empty:
            break
    return events


def run_external_job(
    *,
    job_label: str,
    command: Sequence[str],
    cwd: Path,
    output_path: Path,
    prepare: Callable[[], None] | None = None,
    validator: Callable[[], None] | None = None,
) -> tuple[str, list[Path]]:
    """Start a single-step QE job and return its run id plus log paths."""
    return run_chained_jobs(
        job_label=job_label,
        steps=[
            JobStep(
                label=job_label,
                command=command,
                cwd=Path(cwd),
                output_path=Path(output_path),
                prepare=prepare,
                validator=validator,
            )
        ],
    )


def run_chained_jobs(
    *,
    job_label: str,
    steps: Sequence[JobStep],
    observer: Callable[[dict], None] | None = None,
    cancel_event: threading.Event | None = None,
    runtime_id: str | None = None,
    process_group: bool = False,
    pass_fds: tuple[int, ...] = (),
) -> tuple[str, list[Path]]:
    """Start one or more QE job steps and return the run id plus log paths."""
    if not steps:
        raise ValueError("At least one job step is required.")

    run_id = runtime_id or uuid.uuid4().hex
    normalized_steps = [
        JobStep(
            label=step.label,
            command=tuple(step.command),
            cwd=Path(step.cwd),
            output_path=Path(step.output_path),
            prepare=step.prepare,
            validator=step.validator,
        )
        for step in steps
    ]
    output_paths = [step.output_path for step in normalized_steps]

    # Project observers persist events/logs; their UI does not drain this queue.
    # Keep only a bounded optional stream while preserving legacy queue behavior.
    event_queue: queue.Queue[RunEvent] = queue.Queue(maxsize=DEFAULT_MAX_TAIL_LINES if observer else 0)

    def emit(event):
        try:
            event_queue.put_nowait(event)
        except queue.Full:
            try:
                event_queue.get_nowait()
            except queue.Empty:
                pass
            event_queue.put_nowait(event)

    context = RunContext(
        process=None,
        thread=threading.Thread(target=lambda: None),
        queue=event_queue,
        started_at=datetime.now(timezone.utc),
    )


    def output_lines(process):
        if not process_group:
            yield from process.stdout
            return
        # Some MPI helpers inherit stdout and outlive pw.x. A blocking readline
        # would otherwise prevent cancellation/completion from ever being recorded.
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                ready = selector.select(timeout=.1)
                if not ready and process.poll() is None:
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    if process.poll() is not None:
                        break
                    continue
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                while '\n' in pending:
                    line, pending = pending.split('\n', 1)
                    yield line + '\n'
            pending += decoder.decode(b'', final=True)
            if pending:
                yield pending

    def worker() -> None:
        process: subprocess.Popen[str] | None = None
        current_output_path = output_paths[0]
        ordinal = 0
        outcome = 'failed'
        final_code = DEFAULT_FAILURE_RETURNCODE
        process_code = None
        message = None

        def cancelled():
            return cancel_event is not None and cancel_event.is_set()

        def observe(event, **payload):
            if observer:
                observer(dict(event=event, ordinal=ordinal, **payload))

        try:
            for ordinal, step in enumerate(normalized_steps, 1):
                if cancelled():
                    outcome = 'cancelled'
                    break
                current_output_path = step.output_path
                current_output_path.parent.mkdir(parents=True, exist_ok=True)
                current_output_path.write_text('', encoding='utf-8')
                process_code = None
                observe('step_started')
                emit(RunEvent(type=EVENT_TYPE_PHASE, phase_label=step.label,
                    output_path=str(current_output_path), line=f"\n=== {step.label} ===\n"))
                if step.prepare is not None:
                    step.prepare()
                if cancelled():
                    outcome = 'cancelled'
                    break
                with current_output_path.open('a', encoding='utf-8') as output_file:
                    options = {}
                    if process_group:
                        options.update(start_new_session=True, pass_fds=pass_fds)
                    process = subprocess.Popen(list(step.command), cwd=str(step.cwd), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, bufsize=1, **options)
                    context.process = process
                    if observer:
                        observe('process_started', pid=process.pid)
                    if cancelled() and process_group:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    if process.stdout is None:
                        raise RuntimeError('Quantum ESPRESSO process did not expose stdout.')
                    for line in output_lines(process):
                        output_file.write(line)
                        output_file.flush()
                        emit(RunEvent(type=EVENT_TYPE_LINE, line=line))
                    process_code = process.wait()
                    observe('process_exited', returncode=process_code)
                if process.stdout is not None:
                    process.stdout.close()
                context.process = None
                process = None
                if cancelled():
                    outcome = 'cancelled'
                    final_code = process_code if process_code else DEFAULT_FAILURE_RETURNCODE
                    break
                if process_code != 0:
                    final_code = process_code
                    message = f'Process exited with code {process_code}.'
                    break
                if step.validator is not None:
                    step.validator()
                observe('step_completed', returncode=process_code)
            else:
                outcome = 'completed'
                final_code = 0
        except Exception as exc:
            message = f'Failed to run Quantum ESPRESSO job: {exc}\n'
            try:
                current_output_path.parent.mkdir(parents=True, exist_ok=True)
                with current_output_path.open('a', encoding='utf-8') as output_file:
                    output_file.write(message)
            except OSError:
                pass
            emit(RunEvent(type=EVENT_TYPE_LINE, line=message))
        finally:
            if process is not None:
                if process_group and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    except ProcessLookupError:
                        pass
                if process.stdout is not None:
                    process.stdout.close()
            context.process = None
            try:
                observe('complete', state=outcome, returncode=process_code, message=message)
            finally:
                emit(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=final_code))

    worker_thread = threading.Thread(
        target=worker,
        name=f"qe-run-{run_id}",
        daemon=True,
    )
    context.thread = worker_thread
    RUN_REGISTRY[run_id] = context
    worker_thread.start()

    return run_id, output_paths


def start_qe_run(
    *,
    run_dir: Path,
    command: Sequence[str],
    output_filename: str = "pw.out",
) -> tuple[str, Path]:
    """Backward-compatible wrapper for the original single-step runner."""
    run_id, output_paths = run_external_job(
        job_label="pw.x",
        command=command,
        cwd=Path(run_dir),
        output_path=Path(run_dir) / output_filename,
    )
    return run_id, output_paths[0]
