from __future__ import annotations

from collections.abc import Sequence

from .models import CutoffRecommendation, CutoffStudyPoint, PointResult


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


def recommend_cutoffs(
    study_points: Sequence[CutoffStudyPoint],
    point_results: Sequence[PointResult],
    *,
    tolerance_mev_per_atom: float,
) -> CutoffRecommendation:
    """Apply the conservative tail-stability rule to a completed cutoff sweep."""
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
    deltas_mev_per_atom = [
        abs(energies_per_atom[index] - energies_per_atom[index - 1]) * 1000.0
        for index in range(1, len(energies_per_atom))
    ]

    for candidate_index, candidate_point in enumerate(study_points[:-2]):
        tail_deltas = deltas_mev_per_atom[candidate_index:]
        if len(tail_deltas) < 2:
            continue
        if all(delta <= tolerance_mev_per_atom + _FLOAT_TOLERANCE for delta in tail_deltas):
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

    warnings.append("The highest tested cutoff still does not satisfy the convergence tolerance.")
    return CutoffRecommendation(
        status="unconverged",
        message="Cutoff study is unconverged: extend the sweep to higher cutoffs.",
        warnings=tuple(warnings),
    )
