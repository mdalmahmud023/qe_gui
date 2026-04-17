from __future__ import annotations

from pathlib import Path

from qe_studio.pages import ui_studies
from qe_studio.services import qe_runner

def test_ui_studies_importable():
    assert ui_studies is not None


class _DummyThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _DummyContext:
    def __init__(self, *, alive: bool = True) -> None:
        self.thread = _DummyThread(alive)


def test_sync_study_run_state_updates_running_phase() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-1"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "kpoint"

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="kpoint",
        get_context_fn=lambda _run_id: _DummyContext(alive=True),
        drain_events_fn=lambda _run_id: [
            qe_runner.RunEvent(
                type=qe_runner.EVENT_TYPE_PHASE,
                phase_label="pw.x (k-grid 4x4x4)",
                output_path="qe_runs/kpoint/4x4x4/pw.out",
                line="\n=== pw.x (k-grid 4x4x4) ===\n",
            ),
            qe_runner.RunEvent(type=qe_runner.EVENT_TYPE_LINE, line="total cpu time = 1.23s\n"),
        ],
    )

    assert result == "running"
    assert state[qe_runner.RUN_PHASE_LABEL_STATE_KEY] == "pw.x (k-grid 4x4x4)"
    assert state[qe_runner.RUN_OUTPUT_PATH_STATE_KEY] == "qe_runs/kpoint/4x4x4/pw.out"
    assert state[qe_runner.RUN_TAIL_LINES_STATE_KEY] == [
        "\n=== pw.x (k-grid 4x4x4) ===\n",
        "total cpu time = 1.23s\n",
    ]
    assert state[ui_studies._STUDY_RUN_ID_KEY] == "run-1"


def test_sync_study_run_state_clears_active_keys_when_complete() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-2"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "eos"
    cleaned: list[str | None] = []

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="eos",
        get_context_fn=lambda _run_id: _DummyContext(alive=True),
        drain_events_fn=lambda _run_id: [
            qe_runner.RunEvent(
                type=qe_runner.EVENT_TYPE_PHASE,
                phase_label="pw.x (scale=1.0000)",
                output_path="qe_runs/eos/point_1/pw.out",
                line="\n=== pw.x (scale=1.0000) ===\n",
            ),
            qe_runner.RunEvent(type=qe_runner.EVENT_TYPE_COMPLETE, returncode=0),
        ],
        cleanup_fn=cleaned.append,
    )

    assert result == "completed"
    assert cleaned == ["run-2"]
    assert state[qe_runner.RUN_FINISHED_STATE_KEY] is True
    assert state[qe_runner.RUN_RETURNCODE_STATE_KEY] == 0
    assert ui_studies._STUDY_RUN_ID_KEY not in state
    assert ui_studies._STUDY_RUN_OWNER_KEY not in state


def test_sync_study_run_state_marks_runtime_lost_when_worker_disappears() -> None:
    state: dict[str, object] = {}
    qe_runner.initialize_run_state(state)
    state[ui_studies._STUDY_RUN_ID_KEY] = "run-3"
    state[ui_studies._STUDY_RUN_OWNER_KEY] = "cutoff"
    cleaned: list[str | None] = []

    result = ui_studies._sync_study_run_state(
        state,
        owner_key="cutoff",
        get_context_fn=lambda _run_id: _DummyContext(alive=False),
        drain_events_fn=lambda _run_id: [],
        cleanup_fn=cleaned.append,
    )

    assert result == "runtime_lost"
    assert cleaned == ["run-3"]
    assert state[qe_runner.RUN_FINISHED_STATE_KEY] is True
    assert state[qe_runner.RUN_RETURNCODE_STATE_KEY] == qe_runner.DEFAULT_FAILURE_RETURNCODE
    assert "runtime state was lost" in "".join(state[qe_runner.RUN_TAIL_LINES_STATE_KEY]).lower()
    assert ui_studies._STUDY_RUN_ID_KEY not in state
    assert ui_studies._STUDY_RUN_OWNER_KEY not in state


def test_eos_summary_needs_refresh_when_output_exists(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    point_dir = study_dir / "point_scale_1p0000"
    point_dir.mkdir(parents=True)
    (point_dir / "pw.out").write_text("JOB DONE.\n", encoding="utf-8")

    summary = {
        "points": [
            {
                "run_dir_name": "point_scale_1p0000",
                "status": "missing_output",
            }
        ]
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is True


def test_eos_summary_does_not_refresh_without_matching_output(tmp_path: Path) -> None:
    study_dir = tmp_path / "eos-study"
    study_dir.mkdir()

    summary = {
        "points": [
            {
                "run_dir_name": "point_scale_1p0000",
                "status": "missing_output",
            }
        ]
    }

    assert ui_studies._eos_summary_needs_refresh(study_dir, summary) is False
