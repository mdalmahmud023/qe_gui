from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .models import (
    CONVERGENCE_SCHEMA_VERSION,
    KPOINT_STUDY_MANIFEST_FILENAME,
    KPOINT_STUDY_SUMMARY_FILENAME,
    ConvergenceWorkflowType,
    KPointRecommendation,
    KPointStudyPoint,
    KPointSweepSpec,
    PointResult,
)
from .recommendation import recommend_kpoints
from .result_parser import parse_cutoff_point_output  # Reuse the exact same parser since it extracts total energy
from ..services.input_generator import CalculationContext, generate_qe_input_text
from ..services.reproducibility import (
    ValidationIssue,
    build_generation_validation_issues,
    build_input_reproducibility_metadata,
    resolve_generator_version_info,
    write_input_reproducibility_metadata,
)
from .shared import attach_successive_energy_deltas, deduplicate_messages, make_unique_study_dir


def build_kpoint_study(
    atoms,
    base_ctx: CalculationContext,
    pseudopotentials: dict[str, str],
    pseudo_dir: str | Path,
    *,
    sweep_spec: KPointSweepSpec,
    pseudo_settings: dict[str, Any],
    source_metadata: dict[str, Any],
    structure_signature: dict[str, Any] | None,
    structure_mode: str,
    studies_root: Path | str,
) -> dict[str, Any]:
    """Generate all point inputs and reproducibility records for one k-point study."""
    sweep_spec.validate()

    generator_info = resolve_generator_version_info()
    generated_at = datetime.now(timezone.utc).isoformat()
    study_dir = make_unique_study_dir(studies_root, prefix=base_ctx.control.prefix, generated_at=generated_at)
    study_dir.mkdir(parents=True, exist_ok=True)

    grid_values = sweep_spec.k_point_values()
    point_warnings = build_kpoint_sweep_warnings(sweep_spec, point_count=len(grid_values))

    prepared_points: list[tuple[KPointStudyPoint, CalculationContext, tuple[ValidationIssue, ...]]] = []
    for index, value in enumerate(grid_values, start=1):
        grid = sweep_spec.generate_grid_for_value(value)
        grid_label = f"{grid[0]}x{grid[1]}x{grid[2]}"
        
        point_dir = study_dir / format_kpoint_point_dir_name(grid_label)
        point_ctx = build_kpoint_point_context(base_ctx, grid=grid)
        validation_issues = tuple(
            build_generation_validation_issues(
                atoms,
                point_ctx,
                requested_workflow="scf",
            )
        )
        blocking_errors = [issue.message for issue in validation_issues if issue.level == "error"]
        if blocking_errors:
            raise ValueError(
                "K-point study input generation was blocked by the selected settings: "
                + "; ".join(blocking_errors)
            )

        prepared_points.append(
            (
                KPointStudyPoint(
                    index=index,
                    k_points_grid=grid,
                    grid_label=grid_label,
                    run_dir=point_dir,
                ),
                point_ctx,
                validation_issues,
            )
        )

    generated_points: list[KPointStudyPoint] = []
    for point, point_ctx, validation_issues in prepared_points:
        preview_text, internal_path, ui_path = generate_qe_input_text(
            atoms,
            point_ctx,
            pseudopotentials,
            pseudo_dir,
            run_dir=point.run_dir,
        )
        metadata = build_input_reproducibility_metadata(
            atoms,
            point_ctx,
            requested_workflow="scf",
            pseudo_settings=pseudo_settings,
            source_metadata=source_metadata,
            structure_signature=structure_signature,
            structure_mode=structure_mode,
            validation_issues=validation_issues,
            generator_info=generator_info,
            generated_at=generated_at,
            run_dir=point.run_dir,
        )
        metadata["convergence_study"] = {
            "workflow_type": ConvergenceWorkflowType.KPOINT.value,
            "study_dir": str(study_dir),
            "point_index": point.index,
            "grid_label": point.grid_label,
            "study_manifest_filename": KPOINT_STUDY_MANIFEST_FILENAME,
            "study_summary_filename": KPOINT_STUDY_SUMMARY_FILENAME,
            "preview_length_characters": len(preview_text),
        }
        metadata_path = write_input_reproducibility_metadata(point.run_dir, metadata)
        generated_points.append(
            dataclasses.replace(
                point,
                input_paths={"internal": internal_path, "ui": ui_path},
                metadata_path=metadata_path,
            )
        )

    manifest_payload = build_kpoint_study_manifest(
        atoms,
        base_ctx=base_ctx,
        points=generated_points,
        sweep_spec=sweep_spec,
        pseudo_settings=pseudo_settings,
        source_metadata=source_metadata,
        structure_signature=structure_signature,
        structure_mode=structure_mode,
        generator_info=generator_info,
        generated_at=generated_at,
        study_dir=study_dir,
        study_warnings=point_warnings,
    )
    manifest_path = write_kpoint_study_json(study_dir / KPOINT_STUDY_MANIFEST_FILENAME, manifest_payload)
    summary_payload = refresh_kpoint_study(study_dir)
    return {
        "study_dir": study_dir,
        "manifest_path": manifest_path,
        "summary_path": study_dir / KPOINT_STUDY_SUMMARY_FILENAME,
        "manifest": manifest_payload,
        "summary": summary_payload,
    }


def refresh_kpoint_study(study_dir: Path | str) -> dict[str, Any]:
    """Parse finished point outputs, update the summary JSON, and return the summary payload."""
    study_dir = Path(study_dir)
    manifest = load_kpoint_study_manifest(study_dir)
    points = [KPointStudyPoint.from_dict(payload) for payload in manifest.get("points", [])]
    atom_count = int(manifest["structure"]["atom_count"])
    
    # We can reuse parse_cutoff_point_output because the output parsing is purely identical for scf 
    # to get total energy.
    parsed_results: list[PointResult] = [
        parse_cutoff_point_output(point.run_dir, atom_count=atom_count)
        for point in points
    ]
    parsed_results = attach_successive_energy_deltas(parsed_results)

    recommendation = recommend_kpoints(
        points,
        parsed_results,
        tolerance_mev_per_atom=float(manifest["sweep_spec"]["tolerance_mev_per_atom"]),
    )
    summary_warnings = list(manifest.get("warnings", []))
    for point, result in zip(points, parsed_results, strict=False):
        if result.status != "success":
            summary_warnings.append(
                f"Point {point.index} at grid {point.grid_label} is {result.status.replace('_', ' ')}."
            )
    summary_warnings.extend(recommendation.warnings)

    summary_payload = {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "workflow_type": ConvergenceWorkflowType.KPOINT.value,
        "study_status": recommendation.status,
        "study_dir": str(study_dir),
        "manifest_path": str(study_dir / KPOINT_STUDY_MANIFEST_FILENAME),
        "summary_path": str(study_dir / KPOINT_STUDY_SUMMARY_FILENAME),
        "timestamps": {
            "generated_at": manifest["timestamps"]["generated_at"],
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        },
        "generator": dict(manifest.get("generator", {})),
        "structure": dict(manifest.get("structure", {})),
        "pseudopotentials": dict(manifest.get("pseudopotentials", {})),
        "baseline_cutoffs": dict(manifest.get("baseline_cutoffs", {})),
        "sweep_spec": dict(manifest.get("sweep_spec", {})),
        "recommendation_rule": dict(manifest.get("recommendation_rule", {})),
        "recommendation": recommendation.as_dict(),
        "warnings": deduplicate_messages(summary_warnings),
        "points": [
            {
                **point.as_dict(),
                **result.as_dict(),
            }
            for point, result in zip(points, parsed_results, strict=False)
        ],
    }
    write_kpoint_study_json(study_dir / KPOINT_STUDY_SUMMARY_FILENAME, summary_payload)
    return summary_payload


def load_kpoint_study_manifest(study_dir: Path | str) -> dict[str, Any]:
    """Load one k-point-study manifest from disk."""
    manifest_path = Path(study_dir) / KPOINT_STUDY_MANIFEST_FILENAME
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_kpoint_study_summary(study_dir: Path | str) -> dict[str, Any] | None:
    """Load one k-point-study summary if it exists and is parseable."""
    summary_path = Path(study_dir) / KPOINT_STUDY_SUMMARY_FILENAME
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def discover_kpoint_studies(studies_root: Path | str) -> list[Path]:
    """Return all k-point-study roots sorted from newest to oldest."""
    root = Path(studies_root)
    if not root.is_dir():
        return []
    candidates = [
        candidate
        for candidate in root.iterdir()
        if candidate.is_dir() and (candidate / KPOINT_STUDY_MANIFEST_FILENAME).is_file()
    ]
    return sorted(candidates, reverse=True)


def build_kpoint_point_context(
    base_ctx: CalculationContext,
    *,
    grid: tuple[int, int, int],
) -> CalculationContext:
    """Return a point-specific SCF context while preserving all other settings."""
    return dataclasses.replace(
        base_ctx,
        calculation="scf",
        physical=dataclasses.replace(
            base_ctx.physical,
            k_points_grid=grid,
        ),
    )


def build_kpoint_study_manifest(
    atoms,
    *,
    base_ctx: CalculationContext,
    points: Sequence[KPointStudyPoint],
    sweep_spec: KPointSweepSpec,
    pseudo_settings: dict[str, Any],
    source_metadata: dict[str, Any],
    structure_signature: dict[str, Any] | None,
    structure_mode: str,
    generator_info: dict[str, Any],
    generated_at: str,
    study_dir: Path,
    study_warnings: Sequence[str],
) -> dict[str, Any]:
    """Build the immutable manifest JSON for one generated k-point study."""
    resolved_entries = base_ctx.resolved_entries or {}
    per_species = []
    for species in (base_ctx.species_order or tuple(sorted(resolved_entries))):
        entry = resolved_entries.get(species, {})
        per_species.append(
            {
                "species": species,
                "filename": entry.get("filename"),
                "md5": entry.get("md5"),
                "pseudopotential": entry.get("pseudopotential"),
            }
        )

    return {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "workflow_type": ConvergenceWorkflowType.KPOINT.value,
        "study_dir": str(study_dir),
        "timestamps": {
            "generated_at": generated_at,
        },
        "generator": dict(generator_info),
        "structure": {
            "source_kind": source_metadata.get("source_kind"),
            "source_label": source_metadata.get("source_label"),
            "source_format": source_metadata.get("source_format"),
            "source_details": dict(source_metadata.get("source_details") or {}),
            "structure_signature": structure_signature,
            "formula": atoms.get_chemical_formula(),
            "atom_count": len(atoms),
            "structure_mode": structure_mode,
            "atomic_positions_coordinate_mode": base_ctx.atomic_positions_coordinate_mode,
        },
        "pseudopotentials": {
            "settings": {
                "functional": pseudo_settings.get("functional"),
                "mode": pseudo_settings.get("mode"),
                "version": pseudo_settings.get("version"),
                "fully_relativistic": bool(pseudo_settings.get("fully_relativistic", False)),
                "source": pseudo_settings.get("source"),
            },
            "per_species": per_species,
        },
        "baseline_cutoffs": {
            "ecutwfc": base_ctx.numerical.ecutwfc,
            "ecutrho": base_ctx.numerical.ecutrho,
        },
        "sweep_spec": sweep_spec.as_dict(),
        "tested_grids": [
            {
                "index": point.index,
                "grid_label": point.grid_label,
            }
            for point in points
        ],
        "recommendation_rule": {
            "metric": "total_energy_per_atom_ev",
            "tolerance_mev_per_atom": sweep_spec.tolerance_mev_per_atom,
            "method": "all_higher_points_with_two_confirmation_points",
            "requires_complete_tail": True,
            "minimum_confirmation_points": 2,
        },
        "points": [point.as_dict() for point in points],
        "warnings": list(study_warnings),
    }


def build_kpoint_sweep_warnings(
    sweep_spec: KPointSweepSpec,
    *,
    point_count: int,
) -> list[str]:
    """Return non-fatal scientific warnings about the configured sweep."""
    warnings: list[str] = []
    if point_count < 4:
        warnings.append(
            "The k-point sweep is narrow; fewer than four tested points can make the convergence trend harder to judge."
        )
    if point_count < 3:
        warnings.append(
            "Fewer than three tested points were generated, so QE Studio will not make a k-point recommendation."
        )
    return deduplicate_messages(warnings)


def format_kpoint_point_dir_name(grid_label: str) -> str:
    """Return a filesystem-safe point directory name."""
    return f"point_grid_{grid_label}"


def write_kpoint_study_json(path: Path | str, payload: dict[str, Any]) -> Path:
    """Write one kpoint-study JSON payload deterministically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
