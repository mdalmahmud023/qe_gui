from __future__ import annotations

from pathlib import Path
import re

from .models import CUTOFF_POINT_OUTPUT_FILENAME, PointResult


RY_TO_EV = 13.605693122994
TOTAL_ENERGY_PATTERN = re.compile(
    r"!\s+total energy\s+=\s+([-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)\s+Ry"
)
JOB_DONE_MARKER = "JOB DONE."


def parse_cutoff_point_output(
    run_dir: Path | str,
    *,
    atom_count: int | None,
    output_filename: str = CUTOFF_POINT_OUTPUT_FILENAME,
) -> PointResult:
    """Parse one QE ``pw.out`` and extract the final total energy."""
    run_dir = Path(run_dir)
    output_path = run_dir / output_filename

    try:
        atom_count_value = int(atom_count) if atom_count is not None else None
    except (TypeError, ValueError):
        atom_count_value = None

    if not output_path.is_file():
        return PointResult(
            status="missing_output",
            warnings=("Run output is missing.",),
            output_path=output_path,
            job_done=False,
        )

    try:
        text = output_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return PointResult(
            status="parse_error",
            warnings=(f"Failed to read the QE output: {exc}.",),
            output_path=output_path,
            job_done=False,
        )

    job_done = JOB_DONE_MARKER in text
    if not job_done:
        return PointResult(
            status="missing_job_done",
            warnings=("Run output does not contain `JOB DONE.`.",),
            output_path=output_path,
            job_done=False,
        )

    matches = TOTAL_ENERGY_PATTERN.findall(text)
    if not matches:
        return PointResult(
            status="missing_total_energy",
            warnings=("Run output does not contain a parsable final total energy.",),
            output_path=output_path,
            job_done=True,
        )

    total_energy_ry = float(matches[-1])
    total_energy_ev = total_energy_ry * RY_TO_EV
    warnings: tuple[str, ...] = ()
    energy_per_atom_ev: float | None = None
    if atom_count_value is not None and atom_count_value > 0:
        energy_per_atom_ev = total_energy_ev / atom_count_value
    else:
        warnings = ("Atom count is unavailable or invalid, so per-atom energy could not be derived.",)

    return PointResult(
        status="success",
        total_energy_ry=total_energy_ry,
        total_energy_ev=total_energy_ev,
        energy_per_atom_ev=energy_per_atom_ev,
        warnings=warnings,
        output_path=output_path,
        job_done=True,
    )
