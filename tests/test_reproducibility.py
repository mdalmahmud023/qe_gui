from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from ase import Atoms
from ase.build import bulk, fcc100

from qe_studio.services.input_generator import (
    CalculationContext,
    HubbardUSettings,
    HubbardUSpeciesParameter,
    NumericalSettings,
    PhysicalOptions,
    VdwMethod,
    VdwSettings,
    build_calculation_context_from_request,
    classify_structure,
    get_atomic_species_order,
    make_default_context,
    select_cutoffs,
)
from qe_studio.services.reproducibility import (
    ValidationIssue,
    build_generation_validation_issues,
    build_input_reproducibility_metadata,
    write_input_reproducibility_metadata,
)


def _resolved_entries() -> dict[str, dict[str, object]]:
    return {
        "Fe": {
            "filename": "Fe.test.UPF",
            "md5": "fe-md5",
            "pseudopotential": "PAW",
            "cutoff_wfc": 60.0,
            "cutoff_rho": 480.0,
        },
        "O": {
            "filename": "O.test.UPF",
            "md5": "o-md5",
            "pseudopotential": "PAW",
            "cutoff_wfc": 50.0,
            "cutoff_rho": 400.0,
        },
    }


def _feo_atoms() -> Atoms:
    return Atoms(
        "FeO",
        positions=[[0.0, 0.0, 0.0], [2.1, 2.1, 2.1]],
        cell=[4.3, 4.3, 4.3],
        pbc=True,
    )


class TestGenerationValidationIssues:
    def test_warns_for_low_cutoffs(self):
        atoms = _feo_atoms()
        default_ctx = make_default_context(atoms, resolved_entries=_resolved_entries(), apply_policy=False)
        low_cutoff_ctx = dataclasses.replace(
            default_ctx,
            numerical=NumericalSettings(
                ecutwfc=40.0,
                ecutrho=200.0,
                conv_thr=default_ctx.numerical.conv_thr,
                mixing_beta=default_ctx.numerical.mixing_beta,
            ),
        )

        issues = build_generation_validation_issues(atoms, low_cutoff_ctx, requested_workflow="scf")

        assert any(issue.code == "low_cutoff" and issue.level == "warning" for issue in issues)

    def test_warns_for_very_coarse_kmesh(self):
        atoms = bulk("Si", "diamond", a=5.43, cubic=False)
        default_ctx = make_default_context(atoms)
        coarse_ctx = dataclasses.replace(
            default_ctx,
            physical=dataclasses.replace(default_ctx.physical, k_points_grid=[1, 1, 1]),
        )

        issues = build_generation_validation_issues(atoms, coarse_ctx, requested_workflow="scf")

        assert any(issue.code == "coarse_kmesh" for issue in issues)

    def test_warns_for_low_slab_vacuum(self):
        slab = fcc100("Al", size=(1, 1, 3), vacuum=4.0)
        slab.set_chemical_symbols(["Si"] * len(slab))
        ctx = make_default_context(slab)

        issues = build_generation_validation_issues(slab, ctx, requested_workflow="scf")

        assert any(issue.code == "low_slab_vacuum" for issue in issues)

    def test_warns_for_magnetic_species_with_nonmagnetic_setup(self):
        atoms = bulk("Fe", "bcc", a=2.87)
        ctx = make_default_context(atoms, apply_policy=False)

        issues = build_generation_validation_issues(atoms, ctx, requested_workflow="scf")

        assert any(issue.code == "magnetic_species_nonspin" for issue in issues)

    def test_warns_for_unknown_electronic_character_fallback(self):
        atoms = _feo_atoms()
        ctx = make_default_context(atoms, apply_policy=False)

        issues = build_generation_validation_issues(atoms, ctx, requested_workflow="scf")

        assert any(issue.code == "unknown_electronic_character" for issue in issues)

    def test_errors_for_soc_with_incompatible_pseudos(self, tmp_path: Path):
        atoms = bulk("Fe", "bcc", a=2.87)
        upf_path = tmp_path / "Fe_scalar.UPF"
        upf_path.write_text('<UPF><PP_HEADER has_so="F" relativistic="scalar" /></UPF>', encoding="utf-8")
        ctx = CalculationContext(
            calculation="scf",
            numerical=NumericalSettings(),
            physical=PhysicalOptions(noncolin=True, lspinorb=True),
            classification=classify_structure(atoms),
            cutoff_decision=select_cutoffs({}),
            resolved_entries={"Fe": {"filename": upf_path.name, "local_path": str(upf_path)}},
            species_order=get_atomic_species_order(atoms),
        )

        issues = build_generation_validation_issues(atoms, ctx, requested_workflow="scf")

        assert any(issue.code == "soc_incompatible_pseudos" and issue.level == "error" for issue in issues)

    def test_warns_for_dftu_enabled_without_positive_u(self):
        atoms = _feo_atoms()
        ctx = build_calculation_context_from_request(
            atoms,
            {
                "calculation": "scf",
                "prefix": "feo",
                "outdir": "./tmp",
                "ecutwfc": 60.0,
                "ecutrho": 480.0,
                "occupations": "smearing",
                "smearing": "gaussian",
                "degauss": 0.01,
                "k_points_grid": [4, 4, 4],
                "conv_thr": 1.0e-6,
                "mixing_beta": 0.7,
                "nspin": 1,
                "starting_magnetization": {},
                "noncolin": False,
                "lspinorb": False,
                "dftu_enabled": True,
                "hubbard_u": {
                    "Fe": {"species": "Fe", "u_ev": 0.0, "source": "user_defined"},
                },
                "vdw_method": VdwMethod.NONE.value,
            },
            resolved_entries=_resolved_entries(),
        )

        issues = build_generation_validation_issues(atoms, ctx, requested_workflow="scf")

        assert any(issue.code == "dftu_missing_positive_u" and issue.level == "warning" for issue in issues)

    def test_errors_for_conflicting_vdw_state(self):
        atoms = _feo_atoms()
        ctx = build_calculation_context_from_request(
            atoms,
            {
                "calculation": "scf",
                "prefix": "feo",
                "outdir": "./tmp",
                "ecutwfc": 60.0,
                "ecutrho": 480.0,
                "occupations": "smearing",
                "smearing": "gaussian",
                "degauss": 0.01,
                "k_points_grid": [4, 4, 4],
                "conv_thr": 1.0e-6,
                "mixing_beta": 0.7,
                "nspin": 1,
                "starting_magnetization": {},
                "noncolin": False,
                "lspinorb": False,
                "dftu_enabled": False,
                "hubbard_u": {},
                "vdw_method": VdwMethod.D3_ZERO.value,
                "vdw_state": {"vdw_corr": "grimme-d3", "input_dft": "vdW-DF"},
            },
            resolved_entries=_resolved_entries(),
        )

        issues = build_generation_validation_issues(atoms, ctx, requested_workflow="scf")

        assert any(issue.code == "vdw_conflicting_state" and issue.level == "error" for issue in issues)


class TestInputReproducibilityMetadata:
    def test_metadata_contains_expected_sections_and_override_flags(self):
        atoms = _feo_atoms()
        custom_ctx = build_calculation_context_from_request(
            atoms,
            {
                "calculation": "scf",
                "prefix": "FeO Project",
                "outdir": "./tmp",
                "ecutwfc": 55.0,
                "ecutrho": 420.0,
                "occupations": "fixed",
                "smearing": "gaussian",
                "degauss": 0.01,
                "k_points_grid": [2, 2, 2],
                "conv_thr": 1.0e-6,
                "mixing_beta": 0.7,
                "nspin": 2,
                "starting_magnetization": {
                    "Fe": {"species": "Fe", "value": 0.5, "source": "user_defined"},
                },
                "noncolin": False,
                "lspinorb": False,
                "dftu_enabled": True,
                "hubbard_u": {
                    "Fe": {"species": "Fe", "u_ev": 4.0, "source": "user_defined"},
                },
                "vdw_method": VdwMethod.D3_ZERO.value,
            },
            resolved_entries=_resolved_entries(),
        )
        issues = [
            ValidationIssue(
                code="low_cutoff",
                level="warning",
                message="Selected cutoffs are below the recommended values.",
                details={"selected_ecutwfc_ry": 55.0},
            )
        ]

        metadata = build_input_reproducibility_metadata(
            atoms,
            custom_ctx,
            requested_workflow="scf",
            pseudo_settings={
                "functional": "PBE",
                "mode": "efficiency",
                "version": "1.3.0",
                "fully_relativistic": False,
            },
            source_metadata={
                "source_kind": "file",
                "source_label": "FeO.cif",
                "source_format": "cif",
                "source_details": {"path": "FeO.cif"},
            },
            structure_signature={"hash": "abc123", "elements": ["Fe", "O"]},
            structure_mode="source",
            validation_issues=issues,
            run_dir=Path("/tmp/qe_runs"),
            generator_info={
                "name": "QE Studio",
                "version_source": "git_sha",
                "version": "deadbeef",
                "version_available": True,
            },
            generated_at="2026-04-13T00:00:00+00:00",
        )

        assert metadata["schema_version"] == 1
        assert metadata["timestamps"]["generated_at"] == "2026-04-13T00:00:00+00:00"
        assert metadata["generator"]["version"] == "deadbeef"
        assert metadata["structure"]["source_label"] == "FeO.cif"
        assert metadata["control"]["requested_prefix"] == "FeO Project"
        assert metadata["control"]["rendered_prefix"] == "feo_project"
        assert metadata["control"]["rendered_outdir"] == "./tmp"
        assert metadata["pseudopotentials"]["settings"]["version"] == "1.3.0"
        assert metadata["pseudopotentials"]["per_species"][0]["family_version"] == "1.3.0"
        assert metadata["workflow"]["requested_workflow"] == "scf"
        assert metadata["workflow"]["rendered_calculation_type"] == "scf"
        assert metadata["workflow"]["requested"]["workflow"] == "scf"
        assert metadata["workflow"]["status"] == "rendered"
        assert metadata["cutoffs"]["selection_rule"]["source"] == "sssp_metadata"
        assert metadata["cutoffs"]["user_overridden"] is True
        assert metadata["k_points"]["rule"]["method"] == "workflow_aware_reciprocal_spacing"
        assert metadata["k_points"]["explanation"]
        assert metadata["k_points"]["user_overridden"] is True
        assert metadata["occupations"]["reasons"] == []
        assert metadata["occupations"]["rule"]["method"] == "structure_classification_defaults"
        assert metadata["occupations"]["user_overridden"] is True
        assert metadata["spin"]["nspin"] == 2
        assert metadata["spin"]["requested"]["nspin"] == 2
        assert metadata["soc"]["status"] == "rendered"
        assert metadata["dftu"]["enabled"] is True
        assert metadata["dftu"]["requested"]["enabled"] is True
        assert metadata["vdw"]["method"] == VdwMethod.D3_ZERO.value
        assert metadata["vdw"]["requested"]["method"] == VdwMethod.D3_ZERO.value
        assert metadata["feature_states"]["control"]["status"] == "adjusted"
        assert metadata["validation"]["warning_count"] == 1
        assert metadata["validation"]["error_count"] == 0
        assert len(metadata["validation"]["warnings"]) == 1
        assert metadata["validation"]["errors"] == []

    def test_write_input_reproducibility_metadata_writes_json_file(self, tmp_path: Path):
        payload = {
            "schema_version": 1,
            "generator": {"name": "QE Studio"},
            "validation": {"issues": [], "warning_count": 0, "error_count": 0},
        }

        metadata_path = write_input_reproducibility_metadata(tmp_path, payload)

        assert metadata_path.is_file()
        assert json.loads(metadata_path.read_text(encoding="utf-8")) == payload
