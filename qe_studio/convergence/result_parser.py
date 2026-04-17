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
    atom_count: int,
    output_filename: str = CUTOFF_POINT_OUTPUT_FILENAME,
) -> PointResult:
    """Parse one QE ``pw.out`` and extract the final total energy per atom."""
    run_dir = Path(run_dir)
    output_path = run_dir / output_filename

    if atom_count <= 0:
        raise ValueError("Cutoff-point parsing requires a positive atom count.")

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
    return PointResult(
        status="success",
        total_energy_ry=total_energy_ry,
        energy_per_atom_ev=(total_energy_ry * RY_TO_EV) / atom_count,
        warnings=(),
        output_path=output_path,
        job_done=True,
    )
