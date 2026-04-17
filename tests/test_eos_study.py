from pathlib import Path
import json

import pytest
import numpy as np

from ase import Atoms
from qe_studio.services.input_generator import CalculationContext
from qe_studio.convergence.models import EosSweepSpec, ConvergenceWorkflowType, EOS_STUDY_MANIFEST_FILENAME
from qe_studio.convergence.eos_study import build_eos_study, refresh_eos_study
from qe_studio.convergence.eos_fit import fit_eos


from qe_studio.services.input_generator import CalculationContext, NumericalSettings, PhysicalOptions, ControlSettings

@pytest.fixture
def base_context():
    return CalculationContext(
        calculation="scf",
        numerical=NumericalSettings(ecutwfc=30.0, ecutrho=120.0),
        physical=PhysicalOptions(k_points_grid=[4, 4, 4]),
        control=ControlSettings(prefix="test_eos", requested_prefix="test_eos", outdir=".", requested_outdir=".")
    )

@pytest.fixture
def atoms():
    return Atoms("Si2", cell=[[0.0, 2.7, 2.7], [2.7, 0.0, 2.7], [2.7, 2.7, 0.0]], pbc=True)


def test_eos_sweep_spec_validation():
    with pytest.raises(ValueError, match="Unsupported EOS mode"):
        spec = EosSweepSpec(mode="invalid", start_scale=0.9, stop_scale=1.1, num_points=5)
        spec.validate()

    with pytest.raises(ValueError, match="at least 3 points"):
        spec = EosSweepSpec(mode="isotropic_scale", start_scale=0.9, stop_scale=1.1, num_points=2)
        spec.validate()

    spec = EosSweepSpec(mode="isotropic_scale", start_scale=0.9, stop_scale=1.1, num_points=5)
    scales = spec.scale_values()
    assert len(scales) == 5
    assert scales[0] == 0.9
    assert scales[-1] == 1.1


def test_build_eos_study(atoms, base_context, tmp_path):
    spec = EosSweepSpec(mode="isotropic_scale", start_scale=0.9, stop_scale=1.1, num_points=3)
    baseline = {"k_points_grid": [4, 4, 4], "ecutwfc": 30.0, "ecutrho": 120.0}
    pseudopotentials = {"Si": "Si.upf"}
    study_dir, points = build_eos_study(atoms, base_context, baseline, spec, tmp_path, pseudopotentials, "")

    assert study_dir.is_dir()
    manifest_path = study_dir / EOS_STUDY_MANIFEST_FILENAME
    assert manifest_path.is_file()

    data = json.loads(manifest_path.read_text())
    assert data["study_type"] == ConvergenceWorkflowType.EOS.value
    assert len(data["points"]) == 3

    assert len(points) == 3
    assert points[0].scale_factor == 0.9
    assert points[-1].scale_factor == 1.1

    # Check cell scaling volume
    V_base = atoms.get_volume()
    assert abs(points[0].volume - V_base * (0.9 ** 3)) < 1e-6


def test_eos_fit():
    # Construct a synthetic Birch-Murnaghan curve
    from qe_studio.convergence.eos_fit import birch_murnaghan
    E0_true = -10.0
    V0_true = 50.0
    B0_ev_ang3 = 0.5  # ~80 GPa
    B0_prime = 4.0
    
    volumes = np.linspace(45, 55, 7)
    energies = [birch_murnaghan(v, E0_true, V0_true, B0_ev_ang3, B0_prime) for v in volumes]
    
    fit_result = fit_eos(list(volumes), energies)
    assert fit_result.success
    assert fit_result.available
    assert abs(fit_result.E0 - E0_true) < 1e-4
    assert abs(fit_result.V0 - V0_true) < 1e-4
    
    # Insufficient points
    res = fit_eos([1, 2, 3], [-1, -2, -1])
    assert not res.success
    assert "Insufficient points" in res.errors[0]


def test_eos_fit_edge_bias():
    volumes = [50, 51, 52, 53, 54]
    energies = [-10, -9, -8, -7, -6]  # Minimum is at edge (index 0)
    
    fit_result = fit_eos(volumes, energies)
    # the fit might succeed or fail depending on curve_fit bounds, but there should be a warning
    assert any("edge of the sampled" in w for w in fit_result.warnings)
