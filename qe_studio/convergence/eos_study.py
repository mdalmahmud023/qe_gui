import json
import logging
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ..services.input_generator import generate_qe_input_text, CalculationContext
from .models import (
    CONVERGENCE_SCHEMA_VERSION,
    EOS_POINT_OUTPUT_FILENAME,
    EOS_STUDY_MANIFEST_FILENAME,
    EOS_STUDY_SUMMARY_FILENAME,
    ConvergenceWorkflowType,
    EosStudyPoint,
    EosSweepSpec,
    PointResult,
)
from .shared import make_unique_study_dir
from .result_parser import parse_cutoff_point_output  # Reuse for energy extraction

logger = logging.getLogger(__name__)


def _format_eos_point_dir_name(point: EosStudyPoint) -> str:
    """Format the directory name for an EOS study point."""
    scale_str = f"{point.scale_factor:.4f}".replace(".", "p")
    return f"point_scale_{scale_str}"


def build_eos_study(
    atoms,
    base_context: CalculationContext,
    baseline: dict,
    sweep_spec: EosSweepSpec,
    studies_dir: Path,
    pseudopotentials: dict[str, str],
    pseudo_dir: str,
) -> tuple[Path, list[EosStudyPoint]]:
    """Generate an Equation of State (EOS) study based on isotropic scaling."""
    sweep_spec.validate()

    study_dir = make_unique_study_dir(
        studies_dir,
        prefix=base_context.control.prefix,
        generated_at=datetime.now(timezone.utc).isoformat()
    )
    study_dir.mkdir(parents=True, exist_ok=True)

    points: list[EosStudyPoint] = []
    scale_values = sweep_spec.scale_values()

    for idx, scale in enumerate(scale_values):
        # Isotropic scaling using ASE
        scaled_atoms = atoms.copy()
        scaled_atoms.set_cell(scaled_atoms.cell * scale, scale_atoms=True)
        
        # Calculate resulting volume
        volume = scaled_atoms.get_volume()
        
        point = EosStudyPoint(
            index=idx,
            scale_factor=scale,
            volume=volume,
            label=f"Scale: {scale:.4f}",
            run_dir=study_dir / f"temp_{idx}",  # Will rename after formatting
        )



        point_dir_name = _format_eos_point_dir_name(point)
        point_dir = study_dir / point_dir_name
        point_dir.mkdir(exist_ok=True)
        
        point = EosStudyPoint(
            index=point.index,
            scale_factor=point.scale_factor,
            volume=point.volume,
            label=point.label,
            run_dir=point_dir,
        )
        points.append(point)

        generate_qe_input_text(
            scaled_atoms,
            base_context,
            pseudopotentials,
            pseudo_dir,
            run_dir=point_dir,
        )

    # Write manifest
    manifest_path = study_dir / EOS_STUDY_MANIFEST_FILENAME
    manifest = {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "study_type": ConvergenceWorkflowType.EOS.value,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sweep_spec": {
            "mode": sweep_spec.mode,
            "start_scale": sweep_spec.start_scale,
            "stop_scale": sweep_spec.stop_scale,
            "num_points": sweep_spec.num_points,
            "fit_model": sweep_spec.fit_model,
        },
        "points": [
            {
                "index": p.index,
                "scale_factor": p.scale_factor,
                "volume": p.volume,
                "label": p.label,
                "run_dir_name": p.run_dir.name,
            }
            for p in points
        ],
        "atom_count": len(atoms),
        "structure": {
            "formula": atoms.get_chemical_formula(),
            "atom_count": len(atoms),
        },
        "baseline_snapshot": {
            "physics_parameters": baseline,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return study_dir, points


def load_eos_study_manifest(study_dir: Path) -> dict | None:
    """Load and return the parsed EOS study manifest, if valid."""
    manifest_path = study_dir / EOS_STUDY_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text())
        if data.get("study_type") != ConvergenceWorkflowType.EOS.value:
            return None
        return data
    except (json.JSONDecodeError, IOError):
        logger.warning(f"Failed to read EOS manifest in {study_dir}")
        return None


def load_eos_study_summary(study_dir: Path) -> dict | None:
    """Load and return the parsed EOS study summary, if valid."""
    summary_path = study_dir / EOS_STUDY_SUMMARY_FILENAME
    if not summary_path.is_file():
        return None
    try:
        data = json.loads(summary_path.read_text())
        if data.get("study_type") != ConvergenceWorkflowType.EOS.value:
            return None
        return data
    except (json.JSONDecodeError, IOError):
        logger.warning(f"Failed to read EOS summary in {study_dir}")
        return None


def discover_eos_studies(studies_dir: Path) -> list[Path]:
    """Find all valid EOS studies in the convergence directory."""
    studies = []
    if not studies_dir.is_dir():
        return studies
    for item in studies_dir.iterdir():
        if item.is_dir() and (item / EOS_STUDY_MANIFEST_FILENAME).is_file():
            studies.append(item)
    studies.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return studies


def refresh_eos_study(study_dir: Path) -> dict | None:
    """Parse output files for an EOS study and build/update the summary JSON."""
    manifest = load_eos_study_manifest(study_dir)
    if manifest is None:
        return None

    atom_count = int(manifest.get("structure", {}).get("atom_count") or manifest.get("atom_count") or 1)
    results: list[dict] = []
    from .eos_fit import fit_eos  # Lazy import to avoid circular dependency
    
    volumes = []
    energies = []
    
    for point_data in manifest.get("points", []):
        run_dir = study_dir / point_data["run_dir_name"]
        output_file = run_dir / EOS_POINT_OUTPUT_FILENAME
        
        # Parse energy (we can reuse the cutoff parser since it just looks for total energy)
        point_result = parse_cutoff_point_output(output_file, atom_count=atom_count)
        
        result_dict = {
            "index": point_data["index"],
            "scale_factor": point_data["scale_factor"],
            "volume": point_data["volume"],
            "label": point_data["label"],
            "run_dir_name": point_data["run_dir_name"],
            "status": point_result.status,
            "energy_per_atom_ev": point_result.energy_per_atom_ev,
        }
        
        if point_result.status == "success" and point_result.energy_per_atom_ev is not None:
            # We will use total energy per atom or total energy? 
            # The Birch-Murnaghan expects total energy usually, or energy per atom. 
            # E0 will be in whatever units we provide.
            # `parse_cutoff_point_output` returns `energy_per_atom_ev`.
            volumes.append(point_data["volume"])
            energies.append(point_result.energy_per_atom_ev)

        results.append(result_dict)

    # Perform EOS Fitting
    fit_result = fit_eos(volumes, energies, model_name=manifest["sweep_spec"].get("fit_model", "birch_murnaghan"))

    # Determine Study Status
    has_running = any(r["status"] == "missing" for r in results)
    has_failed = any(r["status"] == "failed" for r in results)
    
    if has_running:
        overall_status = "running"
    elif has_failed:
        overall_status = "error"
    elif not fit_result.success:
        overall_status = "error"
    else:
        overall_status = "complete"

    summary = {
        "schema_version": manifest["schema_version"],
        "study_type": manifest["study_type"],
        "created_at": manifest["created_at"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": overall_status,
        "points": results,
        "fit": {
            "available": fit_result.available,
            "success": fit_result.success,
            "model": fit_result.model,
            "E0": fit_result.E0,
            "V0": fit_result.V0,
            "B0": fit_result.B0,
            "B0_prime": fit_result.B0_prime,
            "a0": fit_result.a0,
            "warnings": fit_result.warnings,
            "errors": fit_result.errors,
        },
        "baseline_snapshot": manifest["baseline_snapshot"],
    }

    summary_path = study_dir / EOS_STUDY_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2))
    return summary
