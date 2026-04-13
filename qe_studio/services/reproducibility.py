"""
Reproducibility metadata and generation-time validation for QE inputs.

This module centralizes two related concerns for QE Studio's input generation:

1. Building a dedicated, science-focused metadata payload alongside the
   generated QE inputs.
2. Evaluating advisory warnings and blocking errors from the same
   :class:`~qe_studio.services.input_generator.CalculationContext` used for
   generation.

The functions here are designed to be deterministic for a given structure,
calculation context, and pseudopotential resolution state. The only runtime
dependency is generator-version discovery, which attempts to read the current
git commit hash and falls back cleanly when that information is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Sequence

from ..core.workspace import QE_INPUT_METADATA_FILENAME
from .input_generator import (
    DEFAULT_KPOINT_SPACING_DOS,
    DEFAULT_KPOINT_SPACING_SCF,
    CalculationContext,
    Dimensionality,
    ElectronicCharacter,
    MagneticCharacter,
    StructureClassification,
    classify_structure,
    make_default_context,
    suggest_kpoints_from_cell,
)
from .pseudopotentials import inspect_soc_compatibility

REPRODUCIBILITY_SCHEMA_VERSION = 1
LOW_VACUUM_THRESHOLD_ANGSTROM = 10.0
_FLOAT_TOLERANCE = 1.0e-9


@dataclass(frozen=True)
class ValidationIssue:
    """One typed validation issue produced during QE input preparation."""

    code: str
    level: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.level not in {"warning", "error"}:
            raise ValueError(f"ValidationIssue level must be 'warning' or 'error', got {self.level!r}")

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable representation."""
        return {
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "details": dict(self.details),
        }


def resolve_generator_version_info(repo_root: Path | None = None) -> dict[str, Any]:
    """Return generator version metadata derived from the current git SHA."""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {
            "name": "QE Studio",
            "version_source": "git_sha",
            "version": "unknown",
            "version_available": False,
        }

    version = result.stdout.strip()
    if not version:
        return {
            "name": "QE Studio",
            "version_source": "git_sha",
            "version": "unknown",
            "version_available": False,
        }

    return {
        "name": "QE Studio",
        "version_source": "git_sha",
        "version": version,
        "version_available": True,
    }


def write_input_reproducibility_metadata(
    run_dir: Path | str,
    metadata: dict[str, Any],
    *,
    filename: str = QE_INPUT_METADATA_FILENAME,
) -> Path:
    """Write the dedicated QE input metadata JSON next to the generated inputs."""
    run_dir_path = Path(run_dir)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    metadata_path = run_dir_path / filename
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return metadata_path


def get_workflow_target_kpoint_spacing(requested_workflow: str) -> float:
    """Return the workflow-aware target reciprocal-space spacing in Å^-1."""
    if requested_workflow in {"nscf", "dos", "bands"}:
        return DEFAULT_KPOINT_SPACING_DOS
    return DEFAULT_KPOINT_SPACING_SCF


def build_generation_validation_issues(
    atoms,
    ctx: CalculationContext,
    *,
    requested_workflow: str,
) -> list[ValidationIssue]:
    """Build warning/error issues for one generation attempt."""
    classification = ctx.classification or classify_structure(atoms)
    issues: list[ValidationIssue] = []

    cutoff_decision = ctx.cutoff_decision
    if cutoff_decision is not None:
        low_cutoff_details: dict[str, Any] = {
            "recommended_ecutwfc_ry": cutoff_decision.ecutwfc_ry,
            "recommended_ecutrho_ry": cutoff_decision.ecutrho_ry,
            "selected_ecutwfc_ry": ctx.numerical.ecutwfc,
            "selected_ecutrho_ry": ctx.numerical.ecutrho,
            "cutoff_source": cutoff_decision.source,
        }
        shortfalls: list[str] = []
        if ctx.numerical.ecutwfc + _FLOAT_TOLERANCE < cutoff_decision.ecutwfc_ry:
            shortfalls.append(
                f"ecutwfc {ctx.numerical.ecutwfc:.1f} Ry < recommended {cutoff_decision.ecutwfc_ry:.1f} Ry"
            )
        if ctx.numerical.ecutrho + _FLOAT_TOLERANCE < cutoff_decision.ecutrho_ry:
            shortfalls.append(
                f"ecutrho {ctx.numerical.ecutrho:.1f} Ry < recommended {cutoff_decision.ecutrho_ry:.1f} Ry"
            )
        if shortfalls:
            issues.append(
                ValidationIssue(
                    code="low_cutoff",
                    level="warning",
                    message=(
                        "Selected cutoffs are below the recommended values "
                        f"({cutoff_decision.source}): {'; '.join(shortfalls)}."
                    ),
                    details=low_cutoff_details,
                )
            )

    spacing = get_workflow_target_kpoint_spacing(requested_workflow)
    recommended_kgrid = suggest_kpoints_from_cell(
        atoms,
        classification=classification,
        target_kpoint_spacing=spacing,
    )
    coarse_axes: list[str] = []
    periodic_axes = {
        0: "x",
        1: "y",
        2: "z",
    }
    for axis, axis_label in periodic_axes.items():
        if not classification.periodic_directions[axis]:
            continue
        if axis in classification.vacuum_directions:
            continue
        selected_k = int(ctx.physical.k_points_grid[axis])
        recommended_k = int(recommended_kgrid[axis])
        if selected_k * 2 <= recommended_k:
            coarse_axes.append(f"{axis_label}: selected {selected_k}, recommended {recommended_k}")
    if coarse_axes:
        issues.append(
            ValidationIssue(
                code="coarse_kmesh",
                level="warning",
                message=(
                    "The selected k-point mesh is much coarser than the workflow-aware "
                    f"recommendation ({', '.join(coarse_axes)})."
                ),
                details={
                    "selected_grid": list(ctx.physical.k_points_grid),
                    "recommended_grid": list(recommended_kgrid),
                    "target_spacing_angstrom_inv": spacing,
                },
            )
        )

    if classification.dimensionality == Dimensionality.D2:
        low_vacuum_axes: dict[str, float] = {}
        for axis in classification.vacuum_directions:
            clearance = estimate_vacuum_clearance(atoms, axis)
            if clearance < LOW_VACUUM_THRESHOLD_ANGSTROM:
                low_vacuum_axes[str(axis)] = clearance
        if low_vacuum_axes:
            formatted = ", ".join(
                f"axis {axis}: {clearance:.2f} A"
                for axis, clearance in sorted(low_vacuum_axes.items())
            )
            issues.append(
                ValidationIssue(
                    code="low_slab_vacuum",
                    level="warning",
                    message=(
                        "This slab appears to have limited vacuum separation "
                        f"({formatted}); values below {LOW_VACUUM_THRESHOLD_ANGSTROM:.1f} A can be risky."
                    ),
                    details={
                        "vacuum_axes_angstrom": dict(low_vacuum_axes),
                        "threshold_angstrom": LOW_VACUUM_THRESHOLD_ANGSTROM,
                    },
                )
            )

    if (
        classification.magnetic_character == MagneticCharacter.MAGNETIC
        and ctx.physical.nspin == 1
        and not (ctx.physical.noncolin or ctx.physical.lspinorb)
    ):
        issues.append(
            ValidationIssue(
                code="magnetic_nonspin",
                level="warning",
                message=(
                    "Potentially magnetic species are present, but the calculation is configured "
                    "nonmagnetically."
                ),
                details={
                    "magnetic_elements": list(classification.magnetic_elements),
                    "nspin": ctx.physical.nspin,
                    "noncolin": bool(ctx.physical.noncolin),
                    "lspinorb": bool(ctx.physical.lspinorb),
                },
            )
        )

    if ctx.physical.noncolin or ctx.physical.lspinorb:
        if not ctx.resolved_entries:
            issues.append(
                ValidationIssue(
                    code="soc_incompatible_pseudos",
                    level="error",
                    message=(
                        "Spin-orbit coupling requires resolved pseudopotential metadata with local UPF paths."
                    ),
                    details={},
                )
            )
        else:
            report = inspect_soc_compatibility(ctx.resolved_entries)
            if not report.all_compatible:
                issues.append(
                    ValidationIssue(
                        code="soc_incompatible_pseudos",
                        level="error",
                        message=report.message,
                        details={
                            "records": [
                                {
                                    "element": record.element,
                                    "filename": record.filename,
                                    "compatible": record.compatible,
                                    "reason": record.reason,
                                }
                                for record in report.records
                            ]
                        },
                    )
                )

    return issues


def build_input_reproducibility_metadata(
    atoms,
    ctx: CalculationContext,
    *,
    requested_workflow: str,
    pseudo_settings: dict[str, Any],
    source_metadata: dict[str, Any],
    structure_signature: dict[str, Any] | None,
    structure_mode: str,
    validation_issues: Sequence[ValidationIssue],
    generator_info: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the dedicated reproducibility metadata payload for generated QE inputs."""
    classification = ctx.classification or classify_structure(atoms)
    generated_timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    generator = dict(generator_info or resolve_generator_version_info())
    recommended_defaults = make_default_context(
        atoms,
        calculation=requested_workflow,
        resolved_entries=ctx.resolved_entries,
    )
    recommended_spacing = get_workflow_target_kpoint_spacing(requested_workflow)
    recommended_kgrid = suggest_kpoints_from_cell(
        atoms,
        classification=classification,
        target_kpoint_spacing=recommended_spacing,
    )
    cutoff_decision = ctx.cutoff_decision
    cutoff_selection_rule = cutoff_decision.as_dict() if cutoff_decision is not None else None
    cutoffs_user_overridden = False
    if cutoff_decision is not None:
        cutoffs_user_overridden = (
            abs(ctx.numerical.ecutwfc - cutoff_decision.ecutwfc_ry) > _FLOAT_TOLERANCE
            or abs(ctx.numerical.ecutrho - cutoff_decision.ecutrho_ry) > _FLOAT_TOLERANCE
        )
    kpoints_user_overridden = list(ctx.physical.k_points_grid) != list(recommended_kgrid)
    occupations_user_overridden = any(
        [
            ctx.physical.occupations != recommended_defaults.physical.occupations,
            ctx.physical.smearing != recommended_defaults.physical.smearing,
            abs(ctx.physical.degauss - recommended_defaults.physical.degauss) > _FLOAT_TOLERANCE,
        ]
    )

    resolved_entries = ctx.resolved_entries or {}
    resolved_source = pseudo_settings.get("source")
    if resolved_source is None:
        resolved_source = "sg15_oncv_fr" if pseudo_settings.get("fully_relativistic") else "sssp"
    pseudo_family_version = pseudo_settings.get("version")

    per_species: list[dict[str, Any]] = []
    for species in (ctx.species_order or tuple(sorted(resolved_entries))):
        entry = resolved_entries.get(species, {})
        per_species.append(
            {
                "species": species,
                "filename": entry.get("filename"),
                "family_version": pseudo_family_version,
                "family_source": resolved_source,
                "md5": entry.get("md5"),
                "pseudopotential": entry.get("pseudopotential"),
            }
        )

    validation_issue_dicts = [issue.as_dict() for issue in validation_issues]
    warning_count = sum(1 for issue in validation_issues if issue.level == "warning")
    error_count = sum(1 for issue in validation_issues if issue.level == "error")

    return {
        "schema_version": REPRODUCIBILITY_SCHEMA_VERSION,
        "timestamps": {
            "generated_at": generated_timestamp,
        },
        "generator": generator,
        "structure": {
            "source_kind": source_metadata.get("source_kind"),
            "source_label": source_metadata.get("source_label"),
            "source_format": source_metadata.get("source_format"),
            "source_details": dict(source_metadata.get("source_details") or {}),
            "structure_signature": structure_signature,
            "formula": atoms.get_chemical_formula(),
            "atom_count": len(atoms),
            "structure_mode": structure_mode,
            "atomic_positions_coordinate_mode": ctx.atomic_positions_coordinate_mode,
        },
        "pseudopotentials": {
            "settings": {
                "functional": pseudo_settings.get("functional"),
                "mode": pseudo_settings.get("mode"),
                "version": pseudo_family_version,
                "fully_relativistic": bool(pseudo_settings.get("fully_relativistic", False)),
                "source": resolved_source,
            },
            "per_species": per_species,
        },
        "workflow": {
            "requested_workflow": requested_workflow,
            "rendered_calculation_type": ctx.calculation,
        },
        "cutoffs": {
            "selected_ecutwfc_ry": ctx.numerical.ecutwfc,
            "selected_ecutrho_ry": ctx.numerical.ecutrho,
            "selection_rule": cutoff_selection_rule,
            "user_overridden": cutoffs_user_overridden,
        },
        "k_points": {
            "selected_grid": list(ctx.physical.k_points_grid),
            "selected_shift": list(ctx.physical.k_points_shift),
            "selection_rule": {
                "method": "workflow_aware_reciprocal_spacing",
                "target_spacing_angstrom_inv": recommended_spacing,
                "recommended_grid": list(recommended_kgrid),
                "dimensionality": classification.dimensionality.value,
                "vacuum_directions": list(classification.vacuum_directions),
            },
            "user_overridden": kpoints_user_overridden,
        },
        "occupations": {
            "selected": {
                "occupations": ctx.physical.occupations,
                "smearing": ctx.physical.smearing,
                "degauss": ctx.physical.degauss,
            },
            "selection_rule": {
                "method": "structure_classification_defaults",
                "electronic_character": classification.electronic_character.value,
                "classification_notes": list(classification.notes),
                "recommended": {
                    "occupations": recommended_defaults.physical.occupations,
                    "smearing": recommended_defaults.physical.smearing,
                    "degauss": recommended_defaults.physical.degauss,
                },
            },
            "user_overridden": occupations_user_overridden,
        },
        "spin": {
            "nspin": ctx.physical.nspin,
            "starting_magnetization": dict(ctx.physical.starting_magnetization),
            "magnetic_character": classification.magnetic_character.value,
            "magnetic_elements": list(classification.magnetic_elements),
            "noncolin": bool(ctx.physical.noncolin),
            "lspinorb": bool(ctx.physical.lspinorb),
        },
        "dftu": ctx.hubbard_u.to_metadata(species_order=ctx.species_order),
        "vdw": ctx.vdw.to_metadata(),
        "validation": {
            "issues": validation_issue_dicts,
            "warning_count": warning_count,
            "error_count": error_count,
        },
    }


def estimate_vacuum_clearance(atoms, axis: int) -> float:
    """Estimate the largest vacuum gap along one lattice-vector direction."""
    cell_vector = atoms.cell.array[axis]
    cell_length = math.sqrt(sum(float(component) ** 2 for component in cell_vector))
    if cell_length <= _FLOAT_TOLERANCE:
        return 0.0

    unit_vector = [float(component) / cell_length for component in cell_vector]
    fractional_positions: list[float] = []
    for position in atoms.get_positions():
        projection = sum(float(position[index]) * unit_vector[index] for index in range(3))
        fractional_positions.append((projection / cell_length) % 1.0)

    if not fractional_positions:
        return 0.0

    ordered = sorted(fractional_positions)
    extended = ordered[1:] + [ordered[0] + 1.0]
    largest_gap_fraction = max(
        next_position - current_position
        for current_position, next_position in zip(ordered, extended, strict=False)
    )
    return largest_gap_fraction * cell_length
