from __future__ import annotations

from collections.abc import Sequence

from .models import CutoffRecommendation, CutoffStudyPoint, PointResult, KPointRecommendation, KPointStudyPoint


_FLOAT_TOLERANCE = 1.0e-9


def format_recommendation_message(
    *,
    ecutwfc_ry: float,
    ecutrho_ry: float,
    tolerance_mev_per_atom: float,
) -> str:
    """Return the canonical user-facing recommendation string."""
    return (
        "Recommended production cutoff: "
        f"ecutwfc = {ecutwfc_ry:g} Ry, "
        f"ecutrho = {ecutrho_ry:g} Ry based on tolerance {tolerance_mev_per_atom:g} meV/atom."
    )


def format_kpoint_recommendation_message(
    *,
    grid_label: str,
    tolerance_mev_per_atom: float,
) -> str:
    """Return the canonical user-facing recommendation string for k-points."""
    return (
        f"Recommended production k-point grid: {grid_label} "
        f"based on tolerance {tolerance_mev_per_atom:g} meV/atom."
    )


def recommend_cutoffs(
    study_points: Sequence[CutoffStudyPoint],
    point_results: Sequence[PointResult],
    *,
    tolerance_mev_per_atom: float,
) -> CutoffRecommendation:
    """Pick the first cutoff whose energy is within tolerance of the highest tested point."""
    if len(study_points) != len(point_results):
        raise ValueError("Study points and parsed point results must have the same length.")
    if tolerance_mev_per_atom <= 0:
        raise ValueError("Cutoff recommendation tolerance must be positive.")

    warnings: list[str] = []
    if len(study_points) < 3:
        warnings.append("At least three cutoff points are required before a recommendation can be made.")
        return CutoffRecommendation(
            status="incomplete",
            message="Cutoff study is incomplete: fewer than three points were generated.",
            warnings=tuple(warnings),
        )

    unsuccessful = [
        (point, result)
        for point, result in zip(study_points, point_results, strict=False)
        if result.status != "success"
    ]
    if unsuccessful:
        warnings.append("Some cutoff points are missing, failed, or do not contain a usable final energy.")
        return CutoffRecommendation(
            status="incomplete",
            message="Cutoff study is incomplete: some point results are missing or failed.",
            warnings=tuple(warnings),
        )

    energies_per_atom = [float(result.energy_per_atom_ev) for result in point_results if result.energy_per_atom_ev is not None]
    reference_energy = energies_per_atom[-1]

    for candidate_point, candidate_energy in zip(study_points, energies_per_atom, strict=False):
        delta_mev_per_atom = abs(candidate_energy - reference_energy) * 1000.0
        if delta_mev_per_atom <= tolerance_mev_per_atom + _FLOAT_TOLERANCE:
            return CutoffRecommendation(
                status="complete",
                recommended_ecutwfc_ry=candidate_point.ecutwfc_ry,
                recommended_ecutrho_ry=candidate_point.ecutrho_ry,
                message=format_recommendation_message(
                    ecutwfc_ry=candidate_point.ecutwfc_ry,
                    ecutrho_ry=candidate_point.ecutrho_ry,
                    tolerance_mev_per_atom=tolerance_mev_per_atom,
                ),
                warnings=tuple(warnings),
            )

    raise AssertionError("The highest tested cutoff should always satisfy the reference-point tolerance rule.")


def recommend_kpoints(
    study_points: Sequence[KPointStudyPoint],
    point_results: Sequence[PointResult],
    *,
    tolerance_mev_per_atom: float,
) -> KPointRecommendation:
    """Pick the first k-point grid whose energy is within tolerance of the highest tested point."""
    if len(study_points) != len(point_results):
        raise ValueError("Study points and parsed point results must have the same length.")
    if tolerance_mev_per_atom <= 0:
        raise ValueError("K-point recommendation tolerance must be positive.")

    warnings: list[str] = []
    if len(study_points) < 3:
        warnings.append("At least three k-point grids are required before a recommendation can be made.")
        return KPointRecommendation(
            status="incomplete",
            message="K-point study is incomplete: fewer than three points were generated.",
            warnings=tuple(warnings),
        )

    unsuccessful = [
        (point, result)
        for point, result in zip(study_points, point_results, strict=False)
        if result.status != "success"
    ]
    if unsuccessful:
        warnings.append("Some k-point grids are missing, failed, or do not contain a usable final energy.")
        return KPointRecommendation(
            status="incomplete",
            message="K-point study is incomplete: some point results are missing or failed.",
            warnings=tuple(warnings),
        )

    energies_per_atom = [float(result.energy_per_atom_ev) for result in point_results if result.energy_per_atom_ev is not None]
    reference_energy = energies_per_atom[-1]

    for candidate_point, candidate_energy in zip(study_points, energies_per_atom, strict=False):
        delta_mev_per_atom = abs(candidate_energy - reference_energy) * 1000.0
        if delta_mev_per_atom <= tolerance_mev_per_atom + _FLOAT_TOLERANCE:
            return KPointRecommendation(
                status="complete",
                recommended_k_grid=candidate_point.k_points_grid,
                grid_label=candidate_point.grid_label,
                message=format_kpoint_recommendation_message(
                    grid_label=candidate_point.grid_label,
                    tolerance_mev_per_atom=tolerance_mev_per_atom,
                ),
                warnings=tuple(warnings),
            )

    raise AssertionError("The highest tested k-point grid should always satisfy the reference-point tolerance rule.")
