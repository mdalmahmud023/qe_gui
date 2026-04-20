import json
import logging
import math
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ase import Atoms
from ase.io import read as read_atoms

from ..services.input_generator import generate_qe_input_text, CalculationContext
from ..services.structure_formula import get_preferred_formula
from ..services.structure_tools import make_conventional_atoms
from .models import (
    CONVERGENCE_SCHEMA_VERSION,
    EOS_FIT_ENERGY_BASIS_CELL_TOTAL_EV,
    EOS_FIT_VOLUME_BASIS_CELL_TOTAL_ANG3,
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
_QE_INPUT_CANDIDATES = ("pw.in", "espresso.pwi")


def _serialize_atoms_snapshot(atoms) -> dict[str, object]:
    """Return one JSON-friendly fitted-cell structure snapshot."""
    return {
        "numbers": [int(number) for number in atoms.numbers.tolist()],
        "cell": [[float(component) for component in row] for row in atoms.cell.array.tolist()],
        "scaled_positions": [
            [float(component) for component in row]
            for row in atoms.get_scaled_positions(wrap=True).tolist()
        ],
        "pbc": [bool(value) for value in atoms.pbc],
    }


def _deserialize_atoms_snapshot(snapshot: dict | None) -> Atoms | None:
    """Rebuild one ASE structure from a stored manifest snapshot."""
    if not isinstance(snapshot, dict):
        return None
    try:
        return Atoms(
            numbers=[int(number) for number in snapshot["numbers"]],
            cell=[[float(component) for component in row] for row in snapshot["cell"]],
            scaled_positions=[
                [float(component) for component in row]
                for row in snapshot["scaled_positions"]
            ],
            pbc=[bool(value) for value in snapshot.get("pbc", (True, True, True))],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_point_input_atoms(run_dir: Path) -> Atoms | None:
    """Load one saved QE input structure from a study-point directory."""
    for candidate_name in _QE_INPUT_CANDIDATES:
        candidate_path = Path(run_dir) / candidate_name
        if not candidate_path.is_file():
            continue
        try:
            return read_atoms(candidate_path, format="espresso-in")
        except Exception:
            logger.warning("Failed to parse EOS point input in %s", candidate_path)
    return None


def _load_reference_fitted_cell_atoms(manifest: dict, study_dir: Path) -> tuple[Atoms | None, str | None]:
    """Return the unscaled fitted-cell structure used to generate the EOS study."""
    structure_payload = manifest.get("structure", {})
    snapshot_atoms = _deserialize_atoms_snapshot(structure_payload.get("fitted_cell_snapshot"))
    if snapshot_atoms is not None:
        return snapshot_atoms, "manifest_snapshot"

    for point_data in manifest.get("points", []):
        run_dir_name = point_data.get("run_dir_name")
        if not run_dir_name:
            continue
        point_atoms = _load_point_input_atoms(study_dir / str(run_dir_name))
        if point_atoms is None:
            continue
        try:
            scale_factor = float(point_data.get("scale_factor"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(scale_factor) or scale_factor <= 0:
            continue
        reference_atoms = point_atoms.copy()
        if abs(scale_factor - 1.0) > 1.0e-12:
            reference_atoms.set_cell(reference_atoms.cell * (1.0 / scale_factor), scale_atoms=True)
        return reference_atoms, f"reconstructed_from_{run_dir_name}"

    return None, None


def _summarize_cell_geometry(atoms) -> dict[str, float | int]:
    """Return a compact geometry summary for one equilibrium cell."""
    a, b, c, alpha, beta, gamma = atoms.cell.cellpar()
    return {
        "a": float(a),
        "b": float(b),
        "c": float(c),
        "alpha": float(alpha),
        "beta": float(beta),
        "gamma": float(gamma),
        "volume": float(atoms.get_volume()),
        "atom_count": int(len(atoms)),
    }


def _build_equilibrium_geometry_summary(
    manifest: dict,
    study_dir: Path,
    *,
    fitted_equilibrium_volume: float | None,
) -> dict[str, object]:
    """Derive fitted-cell and standardized conventional-cell geometry at V0."""
    geometry_summary: dict[str, object] = {
        "available": False,
        "source": None,
        "fitted_cell": None,
        "conventional_cell": {
            "available": False,
            "warnings": [],
        },
        "warnings": [],
    }
    if fitted_equilibrium_volume is None:
        geometry_summary["warnings"] = [
            "Equilibrium geometry metadata is unavailable because the EOS fit did not produce V0.",
        ]
        return geometry_summary

    reference_atoms, source_label = _load_reference_fitted_cell_atoms(manifest, study_dir)
    if reference_atoms is None:
        geometry_summary["warnings"] = [
            "Equilibrium geometry metadata is unavailable because QE Studio could not reconstruct the fitted-cell structure for this EOS study.",
        ]
        return geometry_summary

    reference_volume = float(reference_atoms.get_volume())
    if not math.isfinite(reference_volume) or reference_volume <= 0:
        geometry_summary["warnings"] = [
            "Equilibrium geometry metadata is unavailable because the reconstructed fitted-cell volume is invalid.",
        ]
        return geometry_summary

    scale_factor = (float(fitted_equilibrium_volume) / reference_volume) ** (1.0 / 3.0)
    if not math.isfinite(scale_factor) or scale_factor <= 0:
        geometry_summary["warnings"] = [
            "Equilibrium geometry metadata is unavailable because the fitted equilibrium scale factor is invalid.",
        ]
        return geometry_summary

    equilibrium_fitted_cell = reference_atoms.copy()
    equilibrium_fitted_cell.set_cell(equilibrium_fitted_cell.cell * scale_factor, scale_atoms=True)
    fitted_cell_summary = _summarize_cell_geometry(equilibrium_fitted_cell)
    geometry_summary["available"] = True
    geometry_summary["source"] = source_label
    geometry_summary["fitted_cell"] = fitted_cell_summary

    try:
        conventional_result = make_conventional_atoms(equilibrium_fitted_cell)
        conventional_summary = _summarize_cell_geometry(conventional_result.atoms)
        conventional_summary["available"] = True
        conventional_summary["volume_ratio_to_fitted_cell"] = (
            float(conventional_summary["volume"]) / float(fitted_cell_summary["volume"])
        )
        conventional_summary["warnings"] = []
        geometry_summary["conventional_cell"] = conventional_summary
    except ValueError as exc:
        warning_text = (
            "Standardized conventional-cell geometry is unavailable because "
            f"spglib could not determine a conventional cell: {exc}"
        )
        geometry_summary["warnings"] = [warning_text]
        geometry_summary["conventional_cell"] = {
            "available": False,
            "warnings": [warning_text],
        }

    return geometry_summary


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
            "formula": get_preferred_formula(atoms),
            "atom_count": len(atoms),
            "fitted_cell_snapshot": _serialize_atoms_snapshot(atoms),
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

    atom_count_candidates = [
        manifest.get("structure", {}).get("atom_count"),
        manifest.get("atom_count"),
    ]
    atom_count: int | None = None
    for candidate in atom_count_candidates:
        try:
            parsed_value = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed_value > 0:
            atom_count = parsed_value
            break

    results: list[dict] = []
    from .eos_fit import fit_eos  # Lazy import to avoid circular dependency

    volumes: list[float] = []
    total_energies_ev: list[float] = []
    summary_warnings: list[str] = []
    if atom_count is None:
        summary_warnings.append(
            "The fitted-cell atom count is unavailable or invalid, so per-atom EOS display is disabled."
        )
    
    for point_data in manifest.get("points", []):
        run_dir = study_dir / point_data["run_dir_name"]
        point_result = parse_cutoff_point_output(
            run_dir,
            atom_count=atom_count,
            output_filename=EOS_POINT_OUTPUT_FILENAME,
        )

        result_dict = {
            "index": point_data["index"],
            "scale_factor": point_data["scale_factor"],
            "volume": point_data["volume"],
            "label": point_data["label"],
            "run_dir_name": point_data["run_dir_name"],
            "status": point_result.status,
            "total_energy_ry": point_result.total_energy_ry,
            "total_energy_ev": point_result.total_energy_ev,
            "energy_per_atom_ev": point_result.energy_per_atom_ev,
            "output_path": str(point_result.output_path) if point_result.output_path is not None else None,
            "job_done": point_result.job_done,
            "warnings": list(point_result.warnings),
        }

        if point_result.status == "success" and point_result.total_energy_ev is not None:
            volumes.append(point_data["volume"])
            total_energies_ev.append(point_result.total_energy_ev)
        if point_result.warnings:
            summary_warnings.extend(point_result.warnings)

        results.append(result_dict)

    # Perform EOS Fitting
    fit_result = fit_eos(
        volumes,
        total_energies_ev,
        model_name=manifest["sweep_spec"].get("fit_model", "birch_murnaghan"),
    )

    # Determine Study Status
    running_statuses = {"missing_output", "missing_job_done"}
    has_running = any(r["status"] in running_statuses for r in results)
    has_failed = any(r["status"] == "failed" for r in results)
    
    if has_running:
        overall_status = "running"
    elif has_failed:
        overall_status = "error"
    elif not fit_result.success:
        overall_status = "error"
    else:
        overall_status = "complete"

    equilibrium_geometry = _build_equilibrium_geometry_summary(
        manifest,
        study_dir,
        fitted_equilibrium_volume=fit_result.V0,
    )
    conventional_cell_geometry = equilibrium_geometry.get("conventional_cell", {})
    conventional_lattice_a = None
    if isinstance(conventional_cell_geometry, dict) and conventional_cell_geometry.get("available"):
        conventional_lattice_a = conventional_cell_geometry.get("a")

    summary = {
        "schema_version": manifest["schema_version"],
        "study_type": manifest["study_type"],
        "created_at": manifest["created_at"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": overall_status,
        "points": results,
        "warnings": summary_warnings,
        "fit": {
            "available": fit_result.available,
            "success": fit_result.success,
            "model": fit_result.model,
            "E0": fit_result.E0,
            "V0": fit_result.V0,
            "B0": fit_result.B0,
            "B0_prime": fit_result.B0_prime,
            "a0": conventional_lattice_a,
            "natoms_in_fitted_cell": atom_count,
            "energy_basis": EOS_FIT_ENERGY_BASIS_CELL_TOTAL_EV,
            "volume_basis": EOS_FIT_VOLUME_BASIS_CELL_TOTAL_ANG3,
            "equilibrium_geometry": equilibrium_geometry,
            "warnings": fit_result.warnings,
            "errors": fit_result.errors,
        },
        "baseline_snapshot": manifest["baseline_snapshot"],
    }

    summary_path = study_dir / EOS_STUDY_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2))
    return summary
