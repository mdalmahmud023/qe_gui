from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .models import (
    CONVERGENCE_SCHEMA_VERSION,
    CUTOFF_STUDY_MANIFEST_FILENAME,
    CUTOFF_STUDY_SUMMARY_FILENAME,
    ConvergenceWorkflowType,
    CutoffRecommendation,
    CutoffStudyPoint,
    CutoffSweepSpec,
    EcutrhoMode,
    PointResult,
)
from .recommendation import recommend_cutoffs
from .result_parser import parse_cutoff_point_output
from ..services.input_generator import CalculationContext, generate_qe_input_text
from ..services.qe_control import sanitize_qe_prefix
from ..services.reproducibility import (
    ValidationIssue,
    build_generation_validation_issues,
    build_input_reproducibility_metadata,
    resolve_generator_version_info,
    write_input_reproducibility_metadata,
)
from ..services.structure_formula import get_preferred_formula
from .shared import attach_successive_energy_deltas, deduplicate_messages, make_unique_study_dir


_FLOAT_TOLERANCE = 1.0e-9


def build_cutoff_study(
    atoms,
    base_ctx: CalculationContext,
    pseudopotentials: dict[str, str],
    pseudo_dir: str | Path,
    *,
    sweep_spec: CutoffSweepSpec,
    pseudo_settings: dict[str, Any],
    source_metadata: dict[str, Any],
    structure_signature: dict[str, Any] | None,
    structure_mode: str,
    studies_root: Path | str,
) -> dict[str, Any]:
    """Generate all point inputs and reproducibility records for one cutoff study."""
    sweep_spec.validate()
    metadata_dual_info = inspect_metadata_dual_support(base_ctx.resolved_entries or {})
    if sweep_spec.normalized_ecutrho_mode == EcutrhoMode.METADATA_DUAL and not metadata_dual_info["available"]:
        raise ValueError("The selected pseudopotential set does not provide complete cutoff metadata for metadata-dual ecutrho mode.")

    generator_info = resolve_generator_version_info()
    generated_at = datetime.now(timezone.utc).isoformat()
    study_dir = make_unique_study_dir(studies_root, prefix=base_ctx.control.prefix, generated_at=generated_at)
    study_dir.mkdir(parents=True, exist_ok=True)

    ecutwfc_values = sweep_spec.ecutwfc_values()
    point_warnings = build_cutoff_sweep_warnings(
        sweep_spec,
        point_count=len(ecutwfc_values),
        metadata_dual_info=metadata_dual_info,
    )

    prepared_points: list[tuple[CutoffStudyPoint, CalculationContext, tuple[ValidationIssue, ...]]] = []
    for index, ecutwfc_ry in enumerate(ecutwfc_values, start=1):
        ecutrho_ry = resolve_point_ecutrho(
            sweep_spec,
            ecutwfc_ry=ecutwfc_ry,
            resolved_entries=base_ctx.resolved_entries or {},
        )
        point_dir = study_dir / format_cutoff_point_dir_name(ecutwfc_ry=ecutwfc_ry, ecutrho_ry=ecutrho_ry)
        point_ctx = build_cutoff_point_context(base_ctx, ecutwfc_ry=ecutwfc_ry, ecutrho_ry=ecutrho_ry)
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
                "Cutoff study input generation was blocked by the selected settings: "
                + "; ".join(blocking_errors)
            )

        prepared_points.append(
            (
                CutoffStudyPoint(
                    index=index,
                    ecutwfc_ry=ecutwfc_ry,
                    ecutrho_ry=ecutrho_ry,
                    run_dir=point_dir,
                ),
                point_ctx,
                validation_issues,
            )
        )

    generated_points: list[CutoffStudyPoint] = []
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
            "workflow_type": ConvergenceWorkflowType.CUTOFF.value,
            "study_dir": str(study_dir),
            "point_index": point.index,
            "ecutwfc_ry": point.ecutwfc_ry,
            "ecutrho_ry": point.ecutrho_ry,
            "study_manifest_filename": CUTOFF_STUDY_MANIFEST_FILENAME,
            "study_summary_filename": CUTOFF_STUDY_SUMMARY_FILENAME,
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

    manifest_payload = build_cutoff_study_manifest(
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
        metadata_dual_info=metadata_dual_info,
    )
    manifest_path = write_cutoff_study_json(study_dir / CUTOFF_STUDY_MANIFEST_FILENAME, manifest_payload)
    summary_payload = refresh_cutoff_study(study_dir)
    return {
        "study_dir": study_dir,
        "manifest_path": manifest_path,
        "summary_path": study_dir / CUTOFF_STUDY_SUMMARY_FILENAME,
        "manifest": manifest_payload,
        "summary": summary_payload,
    }


def refresh_cutoff_study(study_dir: Path | str) -> dict[str, Any]:
    """Parse finished point outputs, update the summary JSON, and return the summary payload."""
    study_dir = Path(study_dir)
    manifest = load_cutoff_study_manifest(study_dir)
    points = [CutoffStudyPoint.from_dict(payload) for payload in manifest.get("points", [])]
    atom_count = int(manifest["structure"]["atom_count"])
    parsed_results: list[PointResult] = [
        parse_cutoff_point_output(point.run_dir, atom_count=atom_count)
        for point in points
    ]
    parsed_results = attach_successive_energy_deltas(parsed_results)

    recommendation = recommend_cutoffs(
        points,
        parsed_results,
        tolerance_mev_per_atom=float(manifest["sweep_spec"]["tolerance_mev_per_atom"]),
    )
    summary_warnings = list(manifest.get("warnings", []))
    for point, result in zip(points, parsed_results, strict=False):
        if result.status != "success":
            summary_warnings.append(
                f"Point {point.index} at ecutwfc {point.ecutwfc_ry:g} Ry is {result.status.replace('_', ' ')}."
            )
    summary_warnings.extend(recommendation.warnings)

    summary_payload = {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "workflow_type": ConvergenceWorkflowType.CUTOFF.value,
        "study_status": recommendation.status,
        "study_dir": str(study_dir),
        "manifest_path": str(study_dir / CUTOFF_STUDY_MANIFEST_FILENAME),
        "summary_path": str(study_dir / CUTOFF_STUDY_SUMMARY_FILENAME),
        "timestamps": {
            "generated_at": manifest["timestamps"]["generated_at"],
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
        },
        "generator": dict(manifest.get("generator", {})),
        "structure": dict(manifest.get("structure", {})),
        "pseudopotentials": dict(manifest.get("pseudopotentials", {})),
        "fixed_k_points": list(manifest.get("fixed_k_points", [])),
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
    write_cutoff_study_json(study_dir / CUTOFF_STUDY_SUMMARY_FILENAME, summary_payload)
    return summary_payload


def load_cutoff_study_manifest(study_dir: Path | str) -> dict[str, Any]:
    """Load one cutoff-study manifest from disk."""
    manifest_path = Path(study_dir) / CUTOFF_STUDY_MANIFEST_FILENAME
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_cutoff_study_summary(study_dir: Path | str) -> dict[str, Any] | None:
    """Load one cutoff-study summary if it exists and is parseable."""
    summary_path = Path(study_dir) / CUTOFF_STUDY_SUMMARY_FILENAME
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def discover_cutoff_studies(studies_root: Path | str) -> list[Path]:
    """Return all cutoff-study roots sorted from newest to oldest."""
    root = Path(studies_root)
    if not root.is_dir():
        return []
    candidates = [
        candidate
        for candidate in root.iterdir()
        if candidate.is_dir() and (candidate / CUTOFF_STUDY_MANIFEST_FILENAME).is_file()
    ]
    return sorted(candidates, reverse=True)


def inspect_metadata_dual_support(resolved_entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Describe whether all resolved species support metadata-derived dual ratios."""
    missing_species: list[str] = []
    dual_ratios: dict[str, float] = {}
    for species, entry in sorted(resolved_entries.items()):
        try:
            cutoff_wfc = float(entry.get("cutoff_wfc"))
            cutoff_rho = float(entry.get("cutoff_rho"))
        except (TypeError, ValueError):
            missing_species.append(species)
            continue
        if cutoff_wfc <= 0 or cutoff_rho <= 0:
            missing_species.append(species)
            continue
        dual_ratios[species] = cutoff_rho / cutoff_wfc
    warnings: list[str] = []
    if missing_species:
        warnings.append(
            "Mixed pseudopotential cutoff metadata is incomplete for: "
            + ", ".join(missing_species)
            + "."
        )
    return {
        "available": bool(resolved_entries) and not missing_species,
        "missing_species": missing_species,
        "dual_ratios": dual_ratios,
        "warnings": warnings,
    }


def resolve_point_ecutrho(
    sweep_spec: CutoffSweepSpec,
    *,
    ecutwfc_ry: float,
    resolved_entries: dict[str, dict[str, Any]],
) -> float:
    """Resolve one point's ``ecutrho`` from the selected study policy."""
    mode = sweep_spec.normalized_ecutrho_mode
    if mode == EcutrhoMode.METADATA_DUAL:
        dual_info = inspect_metadata_dual_support(resolved_entries)
        if not dual_info["available"]:
            raise ValueError("Metadata-dual ecutrho mode requires complete cutoff metadata for every species.")
        return max(float(ratio) * ecutwfc_ry for ratio in dual_info["dual_ratios"].values())
    if mode == EcutrhoMode.RATIO_RULE:
        return sweep_spec.selected_ratio_rule_value() * ecutwfc_ry
    if mode == EcutrhoMode.MANUAL_RATIO:
        assert sweep_spec.manual_ratio is not None
        return float(sweep_spec.manual_ratio) * ecutwfc_ry
    if mode == EcutrhoMode.MANUAL_ABSOLUTE:
        assert sweep_spec.manual_ecutrho_ry is not None
        return float(sweep_spec.manual_ecutrho_ry)
    raise ValueError(f"Unsupported ecutrho mode {mode!r}.")


def build_cutoff_point_context(
    base_ctx: CalculationContext,
    *,
    ecutwfc_ry: float,
    ecutrho_ry: float,
) -> CalculationContext:
    """Return a point-specific SCF context while preserving all other settings."""
    return dataclasses.replace(
        base_ctx,
        calculation="scf",
        numerical=dataclasses.replace(
            base_ctx.numerical,
            ecutwfc=float(ecutwfc_ry),
            ecutrho=float(ecutrho_ry),
        ),
    )


def build_cutoff_study_manifest(
    atoms,
    *,
    base_ctx: CalculationContext,
    points: Sequence[CutoffStudyPoint],
    sweep_spec: CutoffSweepSpec,
    pseudo_settings: dict[str, Any],
    source_metadata: dict[str, Any],
    structure_signature: dict[str, Any] | None,
    structure_mode: str,
    generator_info: dict[str, Any],
    generated_at: str,
    study_dir: Path,
    study_warnings: Sequence[str],
    metadata_dual_info: dict[str, Any],
) -> dict[str, Any]:
    """Build the immutable manifest JSON for one generated cutoff study."""
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
                "cutoff_wfc": entry.get("cutoff_wfc"),
                "cutoff_rho": entry.get("cutoff_rho"),
            }
        )

    return {
        "schema_version": CONVERGENCE_SCHEMA_VERSION,
        "workflow_type": ConvergenceWorkflowType.CUTOFF.value,
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
            "formula": get_preferred_formula(atoms),
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
            "metadata_dual_support": metadata_dual_info,
        },
        "fixed_k_points": list(base_ctx.physical.k_points_grid),
        "sweep_spec": sweep_spec.as_dict(),
        "tested_cutoffs": [
            {
                "index": point.index,
                "ecutwfc_ry": point.ecutwfc_ry,
                "ecutrho_ry": point.ecutrho_ry,
            }
            for point in points
        ],
        "recommendation_rule": {
            "metric": "total_energy_per_atom_ev",
            "tolerance_mev_per_atom": sweep_spec.tolerance_mev_per_atom,
            "method": "first_within_tolerance_of_highest_tested_point",
            "reference_point": "highest_tested",
            "requires_complete_tail": False,
        },
        "points": [point.as_dict() for point in points],
        "warnings": list(study_warnings),
    }


def build_cutoff_sweep_warnings(
    sweep_spec: CutoffSweepSpec,
    *,
    point_count: int,
    metadata_dual_info: dict[str, Any],
) -> list[str]:
    """Return non-fatal scientific warnings about the configured sweep."""
    from .shared import deduplicate_messages
    warnings: list[str] = []
    if point_count < 4:
        warnings.append(
            "The cutoff sweep is narrow; fewer than four tested points can make the convergence trend harder to judge."
        )
    if point_count < 3:
        warnings.append(
            "Fewer than three tested points were generated, so QE Studio will not make a cutoff recommendation."
        )
    warnings.extend(metadata_dual_info.get("warnings", []))
    if sweep_spec.normalized_ecutrho_mode != EcutrhoMode.METADATA_DUAL and metadata_dual_info.get("warnings"):
        warnings.append(
            "QE Studio defaulted new incomplete-metadata studies to an explicit ratio-based ecutrho policy."
        )
    return deduplicate_messages(warnings)


def format_cutoff_point_dir_name(*, ecutwfc_ry: float, ecutrho_ry: float) -> str:
    """Return a filesystem-safe point directory name."""
    return (
        "point_"
        f"ecutwfc_{_format_cutoff_token(ecutwfc_ry)}_"
        f"ecutrho_{_format_cutoff_token(ecutrho_ry)}"
    )


def write_cutoff_study_json(path: Path | str, payload: dict[str, Any]) -> Path:
    """Write one cutoff-study JSON payload deterministically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _format_cutoff_token(value: float) -> str:
    text = f"{float(value):05.1f}"
    return text.replace(".", "p")
