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

        assert any(issue.code == "magnetic_nonspin" for issue in issues)

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


class TestInputReproducibilityMetadata:
    def test_metadata_contains_expected_sections_and_override_flags(self):
        atoms = _feo_atoms()
        default_ctx = make_default_context(atoms, resolved_entries=_resolved_entries(), apply_policy=False)
        custom_ctx = dataclasses.replace(
            default_ctx,
            numerical=NumericalSettings(
                ecutwfc=55.0,
                ecutrho=420.0,
                conv_thr=default_ctx.numerical.conv_thr,
                mixing_beta=default_ctx.numerical.mixing_beta,
            ),
            physical=PhysicalOptions(
                occupations="fixed",
                smearing="gaussian",
                degauss=0.01,
                k_points_grid=[2, 2, 2],
                nspin=2,
                starting_magnetization={"Fe": 0.5},
            ),
            hubbard_u=HubbardUSettings(
                enabled=True,
                species_parameters={"Fe": HubbardUSpeciesParameter(species="Fe", u_ev=4.0)},
            ),
            vdw=VdwSettings(method=VdwMethod.D3_ZERO),
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
        assert metadata["pseudopotentials"]["settings"]["version"] == "1.3.0"
        assert metadata["pseudopotentials"]["per_species"][0]["family_version"] == "1.3.0"
        assert metadata["workflow"]["requested_workflow"] == "scf"
        assert metadata["workflow"]["rendered_calculation_type"] == "scf"
        assert metadata["cutoffs"]["selection_rule"]["source"] == "sssp_metadata"
        assert metadata["cutoffs"]["user_overridden"] is True
        assert metadata["k_points"]["user_overridden"] is True
        assert metadata["occupations"]["user_overridden"] is True
        assert metadata["spin"]["nspin"] == 2
        assert metadata["dftu"] == custom_ctx.hubbard_u.to_metadata(species_order=custom_ctx.species_order)
        assert metadata["vdw"] == custom_ctx.vdw.to_metadata()
        assert metadata["validation"]["warning_count"] == 1
        assert metadata["validation"]["error_count"] == 0

    def test_write_input_reproducibility_metadata_writes_json_file(self, tmp_path: Path):
        payload = {
            "schema_version": 1,
            "generator": {"name": "QE Studio"},
            "validation": {"issues": [], "warning_count": 0, "error_count": 0},
        }

        metadata_path = write_input_reproducibility_metadata(tmp_path, payload)

        assert metadata_path.is_file()
        assert json.loads(metadata_path.read_text(encoding="utf-8")) == payload
