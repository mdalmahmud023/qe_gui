from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import queue
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
) -> tuple[str, list[Path]]:
    """Start one or more QE job steps and return the run id plus log paths."""
    if not steps:
        raise ValueError("At least one job step is required.")

    run_id = uuid.uuid4().hex
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

    event_queue: queue.Queue[RunEvent] = queue.Queue()
    context = RunContext(
        process=None,
        thread=threading.Thread(target=lambda: None),
        queue=event_queue,
        started_at=datetime.now(timezone.utc),
    )

    def worker() -> None:
        process: subprocess.Popen[str] | None = None
        current_output_path = output_paths[0]
        try:
            for step in normalized_steps:
                current_output_path = step.output_path
                current_output_path.parent.mkdir(parents=True, exist_ok=True)
                current_output_path.write_text("", encoding="utf-8")

                event_queue.put(
                    RunEvent(
                        type=EVENT_TYPE_PHASE,
                        phase_label=step.label,
                        output_path=str(current_output_path),
                        line=f"\n=== {step.label} ===\n",
                    )
                )

                if step.prepare is not None:
                    step.prepare()

                with current_output_path.open("a", encoding="utf-8") as output_file:
                    process = subprocess.Popen(
                        list(step.command),
                        cwd=str(step.cwd),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        bufsize=1,
                    )
                    context.process = process

                    if process.stdout is None:
                        raise RuntimeError("Quantum ESPRESSO process did not expose stdout.")

                    for line in process.stdout:
                        output_file.write(line)
                        output_file.flush()
                        event_queue.put(RunEvent(type=EVENT_TYPE_LINE, line=line))

                    returncode = process.wait()
                    if returncode != 0:
                        event_queue.put(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=returncode))
                        return

                if process.stdout is not None:
                    process.stdout.close()
                context.process = None
                process = None

                if step.validator is not None:
                    step.validator()

            event_queue.put(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=0))
        except Exception as exc:
            message = f"Failed to run Quantum ESPRESSO job: {exc}\n"
            current_output_path.parent.mkdir(parents=True, exist_ok=True)
            with current_output_path.open("a", encoding="utf-8") as output_file:
                output_file.write(message)
                output_file.flush()
            event_queue.put(RunEvent(type=EVENT_TYPE_LINE, line=message))
            event_queue.put(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=DEFAULT_FAILURE_RETURNCODE))
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            context.process = None

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
