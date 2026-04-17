"""
study_runner.py — Build JobStep sequences for running convergence study points.

This module translates a loaded study manifest into a list of JobStep objects
that can be fed directly into `run_chained_jobs`. Each point directory gets
its own step, so the runner executes them sequentially and the live console
streams the combined output.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..services.qe_runner import JobStep

# The command used to run each study point (mirrors QE_RUN_COMMAND)
_PW_EXECUTABLE = "pw.x"
_PW_INPUT_FILE = "pw.in"
_PW_OUTPUT_FILE = "pw.out"


def is_pw_available() -> bool:
    """Return True if pw.x is on PATH."""
    return shutil.which(_PW_EXECUTABLE) is not None


def build_cutoff_study_steps(study_dir: Path, manifest: dict) -> list[JobStep]:
    """Return one JobStep per cutoff study point, ordered by index."""
    points = manifest.get("points", [])
    steps = []
    for point in sorted(points, key=lambda p: p.get("index", 0)):
        # Cutoff / kpoint manifests store run_dir as an absolute path string
        run_dir = Path(point["run_dir"])
        input_file = run_dir / _PW_INPUT_FILE
        if not input_file.is_file():
            continue
        ecutwfc = point.get("ecutwfc_ry", "?")
        ecutrho = point.get("ecutrho_ry", "?")
        label = f"pw.x (ecutwfc={ecutwfc} Ry, ecutrho={ecutrho} Ry)"
        steps.append(
            JobStep(
                label=label,
                command=(_PW_EXECUTABLE, "-in", _PW_INPUT_FILE),
                cwd=run_dir,
                output_path=run_dir / _PW_OUTPUT_FILE,
            )
        )
    return steps


def build_kpoint_study_steps(study_dir: Path, manifest: dict) -> list[JobStep]:
    """Return one JobStep per k-point study point, ordered by index."""
    points = manifest.get("points", [])
    steps = []
    for point in sorted(points, key=lambda p: p.get("index", 0)):
        run_dir = Path(point["run_dir"])
        input_file = run_dir / _PW_INPUT_FILE
        if not input_file.is_file():
            continue
        grid_label = point.get("grid_label", "?")
        label = f"pw.x (k-grid {grid_label})"
        steps.append(
            JobStep(
                label=label,
                command=(_PW_EXECUTABLE, "-in", _PW_INPUT_FILE),
                cwd=run_dir,
                output_path=run_dir / _PW_OUTPUT_FILE,
            )
        )
    return steps


def build_eos_study_steps(study_dir: Path, manifest: dict) -> list[JobStep]:
    """Return one JobStep per EOS study point, ordered by index."""
    points = manifest.get("points", [])
    steps = []
    for point in sorted(points, key=lambda p: p.get("index", 0)):
        run_dir_name = point.get("run_dir_name")
        if not run_dir_name:
            continue
        run_dir = study_dir / run_dir_name
        input_file = run_dir / _PW_INPUT_FILE
        if not input_file.is_file():
            continue
        scale = point.get("scale_factor", "?")
        label = f"pw.x (scale={scale:.4f})" if isinstance(scale, float) else f"pw.x (scale={scale})"
        steps.append(
            JobStep(
                label=label,
                command=(_PW_EXECUTABLE, "-in", _PW_INPUT_FILE),
                cwd=run_dir,
                output_path=run_dir / _PW_OUTPUT_FILE,
            )
        )
    return steps
