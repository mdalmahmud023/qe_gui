from __future__ import annotations

from qe_studio.services.studies.study_summary_view import detect_baseline_drift


def test_detect_baseline_drift_none():
    active_baseline = {
        "resolved_entries": {
            "Si": {"pseudopotential": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"}
        },
        "k_points_grid": [4, 4, 4]
    }
    study_manifest = {
        "structure": {"structure_signature": {"md5": "abc"}},
        "fixed_k_points": [4, 4, 4],
        "pseudopotentials": {
            "per_species": [
                {"species": "Si", "pseudopotential": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"}
            ]
        }
    }
    
    warnings = detect_baseline_drift(active_baseline, study_manifest, {"md5": "abc"})
    assert not warnings


def test_detect_baseline_drift_kpoints():
    active_baseline = {
        "resolved_entries": {
            "Si": {"pseudopotential": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"}
        },
        "k_points_grid": [6, 6, 6]
    }
    study_manifest = {
        "structure": {"structure_signature": {"md5": "abc"}},
        "fixed_k_points": [4, 4, 4],
        "pseudopotentials": {
            "per_species": [
                {"species": "Si", "pseudopotential": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"}
            ]
        }
    }
    
    warnings = detect_baseline_drift(active_baseline, study_manifest, {"md5": "abc"})
    assert len(warnings) == 1
    assert "K-point grid has changed" in warnings[0]


def test_detect_baseline_drift_pseudos():
    active_baseline = {
        "resolved_entries": {
            "Si": {"pseudopotential": "NEW_PSEUDO.UPF"}
        },
        "k_points_grid": [4, 4, 4]
    }
    study_manifest = {
        "structure": {"structure_signature": {"md5": "abc"}},
        "fixed_k_points": [4, 4, 4],
        "pseudopotentials": {
            "per_species": [
                {"species": "Si", "pseudopotential": "Si.pbe-n-rrkjus_psl.1.0.0.UPF"}
            ]
        }
    }
    
    warnings = detect_baseline_drift(active_baseline, study_manifest, {"md5": "abc"})
    assert len(warnings) == 1
    assert "active pseudopotential set has changed" in warnings[0]
