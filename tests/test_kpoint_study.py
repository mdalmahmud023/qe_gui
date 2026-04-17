import pytest
from ase.build import bulk
from pathlib import Path

from qe_studio.convergence.models import (
    KPointSweepSpec,
    DimensionalityMode,
    KPointStudyPoint,
    PointResult,
)
from qe_studio.convergence.recommendation import recommend_kpoints


def test_kpoint_sweep_spec_validation():
    # Valid spec
    spec = KPointSweepSpec(
        mode=DimensionalityMode.ISOTROPIC_3D,
        start_value=2,
        stop_value=8,
        step=2,
        tolerance_mev_per_atom=1.0,
    )
    spec.validate()
    assert spec.k_point_values() == [2, 4, 6, 8]

    # Invalid bounds
    with pytest.raises(ValueError, match="must be positive"):
        KPointSweepSpec("3d_isotropic", 0, 4, 2, 1.0).validate()

    with pytest.raises(ValueError, match="greater than or equal to"):
        KPointSweepSpec("3d_isotropic", 4, 2, 2, 1.0).validate()

    # Overshoot
    with pytest.raises(ValueError, match="must land exactly on the stop value"):
        KPointSweepSpec("3d_isotropic", 2, 7, 2, 1.0).validate()


def test_dimensionality_generation():
    spec_3d = KPointSweepSpec("3d_isotropic", 2, 4, 2, 1.0)
    assert spec_3d.generate_grid_for_value(4) == (4, 4, 4)

    spec_2d = KPointSweepSpec("2d_in_plane", 2, 4, 2, 1.0)
    assert spec_2d.generate_grid_for_value(4) == (4, 4, 1)

    spec_1d = KPointSweepSpec("1d_wire", 2, 4, 2, 1.0)
    assert spec_1d.generate_grid_for_value(4) == (1, 1, 4)


def test_kpoint_recommendation():
    points = [
        KPointStudyPoint(1, (2, 2, 2), "2x2x2", Path("")),
        KPointStudyPoint(2, (4, 4, 4), "4x4x4", Path("")),
        KPointStudyPoint(3, (6, 6, 6), "6x6x6", Path("")),
        KPointStudyPoint(4, (8, 8, 8), "8x8x8", Path("")),
    ]
    
    # 2x2x2: 10.0, 4x4x4: 8.0, 6x6x6: 7.0, 8x8x8: 7.0
    results = [
        PointResult("success", energy_per_atom_ev=10.0, delta_mev_per_atom=None),
        PointResult("success", energy_per_atom_ev=8.0, delta_mev_per_atom=2000.0),
        PointResult("success", energy_per_atom_ev=7.0, delta_mev_per_atom=1000.0),
        PointResult("success", energy_per_atom_ev=7.0, delta_mev_per_atom=0.0),
    ]
    
    rec = recommend_kpoints(points, results, tolerance_mev_per_atom=1.0)
    assert rec.status == "complete"
    assert rec.recommended_k_grid == (6, 6, 6)
    assert rec.grid_label == "6x6x6"

def test_kpoint_recommendation_converged():
    points = [
        KPointStudyPoint(1, (2, 2, 2), "2x2x2", Path("")),
        KPointStudyPoint(2, (4, 4, 4), "4x4x4", Path("")),
        KPointStudyPoint(3, (6, 6, 6), "6x6x6", Path("")),
        KPointStudyPoint(4, (8, 8, 8), "8x8x8", Path("")),
        KPointStudyPoint(5, (10, 10, 10), "10x10x10", Path("")),
    ]
    
    results = [
        PointResult("success", energy_per_atom_ev=10.0),
        PointResult("success", energy_per_atom_ev=8.0),
        PointResult("success", energy_per_atom_ev=7.0),
        PointResult("success", energy_per_atom_ev=7.0),
        PointResult("success", energy_per_atom_ev=7.0),
    ]
    
    rec = recommend_kpoints(points, results, tolerance_mev_per_atom=1.0)
    assert rec.status == "complete"
    assert rec.recommended_k_grid == (6, 6, 6)
    assert rec.grid_label == "6x6x6"


def test_kpoint_recommendation_falls_back_to_highest_tested_grid():
    points = [
        KPointStudyPoint(1, (2, 2, 2), "2x2x2", Path("")),
        KPointStudyPoint(2, (4, 4, 4), "4x4x4", Path("")),
        KPointStudyPoint(3, (6, 6, 6), "6x6x6", Path("")),
    ]
    
    results = [
        PointResult("success", energy_per_atom_ev=10.0),
        PointResult("success", energy_per_atom_ev=8.0),
        PointResult("success", energy_per_atom_ev=6.0),
    ]
    
    rec = recommend_kpoints(points, results, tolerance_mev_per_atom=1.0)
    assert rec.status == "complete"
    assert rec.recommended_k_grid == (6, 6, 6)
    assert rec.grid_label == "6x6x6"
