from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import queue
import subprocess
import threading
import uuid
from typing import MutableMapping, Sequence


RUN_ACTIVE_STATE_KEY = "run_active"
RUN_FINISHED_STATE_KEY = "run_finished"
RUN_RETURNCODE_STATE_KEY = "run_returncode"
RUN_TAIL_LINES_STATE_KEY = "tail_lines"
RUN_OUTPUT_PATH_STATE_KEY = "output_path"
ACTIVE_RUN_ID_STATE_KEY = "active_run_id"
RUN_STATE_KEYS = (
    RUN_ACTIVE_STATE_KEY,
    RUN_FINISHED_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    RUN_OUTPUT_PATH_STATE_KEY,
    ACTIVE_RUN_ID_STATE_KEY,
)

EVENT_TYPE_LINE = "line"
EVENT_TYPE_COMPLETE = "complete"
DEFAULT_FAILURE_RETURNCODE = -1
DEFAULT_MAX_TAIL_LINES = 200
RUNTIME_LOST_MESSAGE = (
    "Live Quantum ESPRESSO runtime state was lost. "
    "The app can no longer stream this calculation."
)


@dataclass(frozen=True)
class RunEvent:
    type: str
    line: str | None = None
    returncode: int | None = None


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
    state.setdefault(ACTIVE_RUN_ID_STATE_KEY, None)


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
    state[ACTIVE_RUN_ID_STATE_KEY] = None


def set_run_started(
    state: MutableMapping[str, object],
    *,
    run_id: str,
    output_path: Path,
) -> None:
    """Store a fresh running-state snapshot in session state."""
    initialize_run_state(state)
    state[RUN_ACTIVE_STATE_KEY] = True
    state[RUN_FINISHED_STATE_KEY] = False
    state[RUN_RETURNCODE_STATE_KEY] = None
    state[RUN_TAIL_LINES_STATE_KEY] = []
    state[RUN_OUTPUT_PATH_STATE_KEY] = str(output_path)
    state[ACTIVE_RUN_ID_STATE_KEY] = run_id


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
        if event.type == EVENT_TYPE_LINE and event.line is not None:
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


def start_qe_run(
    *,
    run_dir: Path,
    command: Sequence[str],
    output_filename: str = "pw.out",
) -> tuple[str, Path]:
    """Start a registry-backed QE subprocess worker and return its run id."""
    run_id = uuid.uuid4().hex
    run_directory = Path(run_dir)
    output_path = run_directory / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    event_queue: queue.Queue[RunEvent] = queue.Queue()
    context = RunContext(
        process=None,
        thread=threading.Thread(target=lambda: None),
        queue=event_queue,
        started_at=datetime.now(timezone.utc),
    )

    def worker() -> None:
        process: subprocess.Popen[str] | None = None
        try:
            with output_path.open("a", encoding="utf-8") as output_file:
                process = subprocess.Popen(
                    list(command),
                    cwd=str(run_directory),
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
                event_queue.put(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=returncode))
        except Exception as exc:
            message = f"Failed to run Quantum ESPRESSO: {exc}\n"
            with output_path.open("a", encoding="utf-8") as output_file:
                output_file.write(message)
                output_file.flush()
            event_queue.put(RunEvent(type=EVENT_TYPE_LINE, line=message))
            event_queue.put(RunEvent(type=EVENT_TYPE_COMPLETE, returncode=DEFAULT_FAILURE_RETURNCODE))
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()

    worker_thread = threading.Thread(
        target=worker,
        name=f"qe-run-{run_id}",
        daemon=True,
    )
    context.thread = worker_thread
    RUN_REGISTRY[run_id] = context
    worker_thread.start()

    return run_id, output_path
