from __future__ import annotations

from typing import Any


def detect_baseline_drift(
    active_baseline: dict[str, Any],
    study_manifest: dict[str, Any],
    active_structure_signature: dict[str, Any] | None = None,
) -> list[str]:
    """Detect if the current Setup baseline differs from the one used for a study.

    Args:
        active_baseline: The current active QE parameters (from get_qe_parameters).
        study_manifest: The loaded cutoff study manifest JSON.
        active_structure_signature: The current active structure signature.

    Returns:
        A list of warning messages explaining how the baseline has drifted.
        An empty list if the baseline still matches the study.
    """
    warnings = []

    # Check Structure
    manifest_structure = study_manifest.get("structure", {})
    manifest_signature = manifest_structure.get("structure_signature")
    if active_structure_signature is not None and manifest_signature is not None:
        if active_structure_signature != manifest_signature:
            warnings.append("The active structural model has changed.")
    
    # Check Pseudopotentials
    manifest_pseudos = study_manifest.get("pseudopotentials", {}).get("per_species", [])
    manifest_pseudo_names = {p.get("species"): p.get("pseudopotential") for p in manifest_pseudos}
    
    active_pseudo_names = {}
    resolved_entries = active_baseline.get("resolved_entries", {})
    for species, entry in resolved_entries.items():
        active_pseudo_names[species] = entry.get("pseudopotential")
        
    if manifest_pseudo_names and active_pseudo_names:
        if active_pseudo_names != manifest_pseudo_names:
            warnings.append("The active pseudopotential set has changed since this study was generated.")
            
    # Check K-Points
    active_k_points = active_baseline.get("k_points_grid")
    manifest_k_points = study_manifest.get("fixed_k_points")
    if active_k_points is not None and manifest_k_points is not None:
        if list(active_k_points) != list(manifest_k_points):
            active_k_str = "x".join(str(k) for k in active_k_points)
            manifest_k_str = "x".join(str(k) for k in manifest_k_points)
            warnings.append(f"K-point grid has changed (Active: {active_k_str}, Study: {manifest_k_str}).")

    return warnings
