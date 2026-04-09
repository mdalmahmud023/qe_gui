"""
Unit tests for ``qe_studio.services.input_generator``.

These tests run without Streamlit, without a live QE installation, and
without network access.  The only external dependencies are ASE, NumPy,
and pytest.

Test organisation
-----------------
TestStructureClassification
    classify_structure() for bulk, slab, molecule, chain, and mixed systems

TestCutoffSuggestion
    suggest_cutoffs_from_pseudo_entries() for various manifest shapes

TestKpointSuggestion
    suggest_kpoints_from_cell() for cubic, hexagonal, slab, and molecule cells

TestNumericalSettings
    NumericalSettings.validate() acceptance and rejection

TestPhysicalOptions
    PhysicalOptions.validate() acceptance and rejection

TestCalculationContext
    CalculationContext.validate() acceptance and rejection

TestDefaultPolicy
    apply_default_policy() rule application

TestBuildQEInputNamelist
    build_qe_input_namelist() structural assertions for all calculation types

TestCleanQEInputText
    clean_qe_input_text() empty-namelist removal

TestMakeDefaultContext
    make_default_context() end-to-end integration without disk I/O
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from ase import Atoms
from ase.build import bulk, molecule

from qe_studio.services.cutoff_presets import CutoffDecision, CutoffPreset
from qe_studio.services.input_generator import (
    DEFAULT_KPOINT_SPACING_SCF,
    FALLBACK_ECUTRHO_RY,
    FALLBACK_ECUTWFC_RY,
    CalculationContext,
    Dimensionality,
    ElectronicCharacter,
    MagneticCharacter,
    NumericalSettings,
    PhysicalOptions,
    StructureClassification,
    apply_default_policy,
    build_qe_input_namelist,
    classify_structure,
    clean_qe_input_text,
    make_default_context,
    select_cutoffs,
    suggest_cutoffs_from_pseudo_entries,
    suggest_kpoints_from_cell,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bulk_silicon() -> Atoms:
    """Return an ASE Si FCC primitive cell (2 atoms)."""
    return bulk("Si", "diamond", a=5.43, cubic=False)


def _make_bulk_iron() -> Atoms:
    """Return an ASE BCC Fe primitive cell (1 atom)."""
    return bulk("Fe", "bcc", a=2.87)


def _make_bulk_mgo() -> Atoms:
    """Return a rocksalt MgO unit cell."""
    return bulk("MgO", crystalstructure="rocksalt", a=4.21)


def _make_slab_silicon(vacuum: float = 15.0) -> Atoms:
    """Return a minimal Si(001) slab with vacuum along z."""
    from ase.build import fcc100

    # Use Al as placeholder for a simple FCC slab geometry
    slab = fcc100("Al", size=(1, 1, 3), vacuum=vacuum)
    slab.set_chemical_symbols(["Si"] * len(slab))
    return slab


def _make_h2_molecule() -> Atoms:
    """Return H2 in a large box (non-periodic)."""
    atoms = Atoms("H2", positions=[[0, 0, 0], [0, 0, 0.74]], cell=[15, 15, 15], pbc=False)
    return atoms


def _make_chain() -> Atoms:
    """Return a 1D periodic chain of Al atoms."""
    atoms = Atoms("Al2", positions=[[0, 0, 0], [0, 0, 2.86]], cell=[15, 15, 5.72], pbc=[False, False, True])
    return atoms


def _make_fe2o3_bulk() -> Atoms:
    """Return a simple Fe2O3 mock (mixed metal+non-metal, magnetic element)."""
    a = 5.0
    atoms = Atoms(
        "Fe2O3",
        positions=[
            [0.0, 0.0, 0.0],
            [a / 2, a / 2, 0.0],
            [0.0, a / 2, a / 2],
            [a / 2, 0.0, a / 2],
            [0.0, 0.0, a / 2],
        ],
        cell=[a, a, a],
        pbc=True,
    )
    return atoms


# ---------------------------------------------------------------------------
# TestStructureClassification
# ---------------------------------------------------------------------------


class TestStructureClassification:
    def test_bulk_si_is_3d_insulator_nonmagnetic(self):
        si = _make_bulk_silicon()
        cls = classify_structure(si)
        assert cls.dimensionality == Dimensionality.D3
        assert cls.electronic_character == ElectronicCharacter.INSULATOR
        assert cls.magnetic_character == MagneticCharacter.NONMAGNETIC
        assert cls.periodic_directions == (True, True, True)
        assert cls.vacuum_directions == ()

    def test_bulk_fe_is_3d_metal_magnetic(self):
        fe = _make_bulk_iron()
        cls = classify_structure(fe)
        assert cls.dimensionality == Dimensionality.D3
        assert cls.electronic_character == ElectronicCharacter.METAL
        assert cls.magnetic_character == MagneticCharacter.MAGNETIC

    def test_molecule_is_0d(self):
        h2 = _make_h2_molecule()
        cls = classify_structure(h2)
        assert cls.dimensionality == Dimensionality.D0
        assert cls.electronic_character == ElectronicCharacter.INSULATOR
        assert cls.magnetic_character == MagneticCharacter.NONMAGNETIC

    def test_chain_is_1d(self):
        chain = _make_chain()
        cls = classify_structure(chain)
        assert cls.dimensionality == Dimensionality.D1

    def test_slab_is_2d(self):
        slab = _make_slab_silicon(vacuum=15.0)
        cls = classify_structure(slab)
        assert cls.dimensionality == Dimensionality.D2

    def test_fe2o3_is_mixed_electronic_character(self):
        fe2o3 = _make_fe2o3_bulk()
        cls = classify_structure(fe2o3)
        # Fe is metallic, O is not → UNKNOWN
        assert cls.electronic_character == ElectronicCharacter.UNKNOWN
        # Fe has d electrons → magnetic
        assert cls.magnetic_character == MagneticCharacter.MAGNETIC

    def test_mgo_is_insulator_nonmagnetic(self):
        mgo = _make_bulk_mgo()
        cls = classify_structure(mgo)
        # Mg is typically metallic, O is not → UNKNOWN (mixed)
        assert cls.electronic_character in (
            ElectronicCharacter.UNKNOWN,
            ElectronicCharacter.INSULATOR,
        )
        assert cls.magnetic_character == MagneticCharacter.NONMAGNETIC

    def test_notes_are_non_empty(self):
        si = _make_bulk_silicon()
        cls = classify_structure(si)
        assert len(cls.notes) > 0
        assert all(isinstance(note, str) for note in cls.notes)

    def test_pure_metal_al_is_metal(self):
        al = bulk("Al", "fcc", a=4.05)
        cls = classify_structure(al)
        assert cls.electronic_character == ElectronicCharacter.METAL
        assert cls.magnetic_character == MagneticCharacter.NONMAGNETIC

    def test_classification_is_frozen(self):
        si = _make_bulk_silicon()
        cls = classify_structure(si)
        with pytest.raises((AttributeError, TypeError)):
            cls.dimensionality = Dimensionality.D0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestCutoffSuggestion
# ---------------------------------------------------------------------------


class TestCutoffSuggestion:
    """Tests for the backward-compatible suggest_cutoffs_from_pseudo_entries() wrapper.

    NOTE on field names: The SSSP manifest uses ``cutoff_wfc`` and ``cutoff_rho``
    (direct Ry values), **not** ``cutoff`` / ``dual``.  All fixtures in this class
    use the correct field names.  The old ``cutoff``/``dual`` fields were a bug in
    the previous implementation and are not read by the current code.
    """

    def test_empty_entries_returns_fallback(self):
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries({})
        assert ecutwfc == FALLBACK_ECUTWFC_RY
        assert ecutrho == FALLBACK_ECUTRHO_RY

    def test_single_element_with_cutoff_wfc_and_cutoff_rho(self):
        # Standard preset (1.00x): expects ≥ manifest value
        entries = {
            "Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0},
        }
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc >= 30.0
        assert ecutrho >= 240.0

    def test_multi_element_takes_max(self):
        entries = {
            "Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0},
            "O":  {"cutoff_wfc": 75.0, "cutoff_rho": 600.0},
        }
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc >= 75.0
        assert ecutrho >= 600.0

    def test_missing_cutoff_rho_defaults_to_4x(self):
        """When cutoff_rho is absent a 4× heuristic is used, not a dual ratio."""
        entries = {"Fe": {"cutoff_wfc": 60.0}}
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc >= 60.0
        assert ecutrho >= 60.0 * 4.0

    def test_returns_floats(self):
        entries = {"Al": {"cutoff_wfc": 25.0, "cutoff_rho": 200.0}}
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert isinstance(ecutwfc, float)
        assert isinstance(ecutrho, float)

    def test_ignores_entry_without_cutoff_wfc(self):
        entries = {
            "X":  {"cutoff_rho": 200.0},          # missing cutoff_wfc
            "Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0},
        }
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc >= 30.0

    def test_rounds_to_nearest_5_ry(self):
        entries = {"Si": {"cutoff_wfc": 33.0, "cutoff_rho": 240.0}}
        ecutwfc, _ = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc % 5 == 0

    def test_never_returns_zero_or_negative(self):
        entries = {"Bad": {"cutoff_wfc": 0.0, "cutoff_rho": 0.0}}
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        assert ecutwfc > 0
        assert ecutrho > 0

    def test_invalid_cutoff_wfc_string_is_skipped(self):
        entries = {"Si": {"cutoff_wfc": "not_a_number", "cutoff_rho": 240.0}}
        ecutwfc, ecutrho = suggest_cutoffs_from_pseudo_entries(entries)
        # Falls back to defaults when the only entry is invalid
        assert ecutwfc == FALLBACK_ECUTWFC_RY

    def test_wrapper_delegates_to_select_cutoffs(self):
        """The wrapper must return the same values as select_cutoffs(STANDARD)."""
        entries = {"Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0}}
        wrapper_wfc, wrapper_rho = suggest_cutoffs_from_pseudo_entries(entries)
        decision = select_cutoffs(entries, preset=CutoffPreset.STANDARD)
        assert wrapper_wfc == decision.ecutwfc_ry
        assert wrapper_rho == decision.ecutrho_ry


# ---------------------------------------------------------------------------
# TestKpointSuggestion
# ---------------------------------------------------------------------------


class TestKpointSuggestion:
    def test_cubic_cell_returns_symmetric_grid(self):
        si = _make_bulk_silicon()
        kgrid = suggest_kpoints_from_cell(si)
        assert len(kgrid) == 3
        assert all(k >= 1 for k in kgrid)

    def test_molecule_box_returns_gamma_only(self):
        h2 = _make_h2_molecule()
        # pbc=False → all directions non-periodic
        kgrid = suggest_kpoints_from_cell(h2)
        assert kgrid == [1, 1, 1]

    def test_chain_returns_1_in_nonperiodic_directions(self):
        chain = _make_chain()
        kgrid = suggest_kpoints_from_cell(chain)
        # Only z is periodic
        assert kgrid[0] == 1
        assert kgrid[1] == 1
        assert kgrid[2] >= 1

    def test_larger_cell_gives_fewer_k_points(self):
        small = Atoms("Al", cell=[3, 3, 3], pbc=True)
        large = Atoms("Al", cell=[6, 6, 6], pbc=True)
        small_k = suggest_kpoints_from_cell(small)
        large_k = suggest_kpoints_from_cell(large)
        # Larger cell → smaller reciprocal-space length → fewer k-points
        assert all(sk >= lk for sk, lk in zip(small_k, large_k))

    def test_smaller_spacing_gives_more_k_points(self):
        si = _make_bulk_silicon()
        coarse = suggest_kpoints_from_cell(si, target_kpoint_spacing=0.20)
        fine = suggest_kpoints_from_cell(si, target_kpoint_spacing=0.05)
        # Smaller spacing means a finer real-space sampling, hence more k-points
        assert all(f >= c for f, c in zip(fine, coarse))

    def test_invalid_spacing_raises(self):
        si = _make_bulk_silicon()
        with pytest.raises(ValueError):
            suggest_kpoints_from_cell(si, target_kpoint_spacing=0.0)
        with pytest.raises(ValueError):
            suggest_kpoints_from_cell(si, target_kpoint_spacing=-1.0)

    def test_returns_list_of_ints(self):
        si = _make_bulk_silicon()
        kgrid = suggest_kpoints_from_cell(si)
        assert all(isinstance(k, int) for k in kgrid)

    def test_classification_flattens_vacuum_directions(self):
        # A slab modeled in a 3D box, pbc=[True, True, True] nominally
        slab = Atoms("Al", cell=[3, 3, 20], pbc=True)
        # But classification knows Z is vacuum
        from qe_studio.services.input_generator import StructureClassification, Dimensionality, ElectronicCharacter, MagneticCharacter
        cls = StructureClassification(
            dimensionality=Dimensionality.D2,
            periodic_directions=(True, True, False),
            vacuum_directions=(2,),
            electronic_character=ElectronicCharacter.METAL,
            magnetic_character=MagneticCharacter.NONMAGNETIC,
            notes=[],
        )
        kgrid = suggest_kpoints_from_cell(slab, classification=cls)
        assert kgrid[2] == 1


# ---------------------------------------------------------------------------
# TestNumericalSettings
# ---------------------------------------------------------------------------


class TestNumericalSettings:
    def test_default_values(self):
        ns = NumericalSettings()
        assert ns.ecutwfc == FALLBACK_ECUTWFC_RY
        assert ns.ecutrho == FALLBACK_ECUTRHO_RY
        assert ns.conv_thr == 1.0e-6
        assert ns.mixing_beta == 0.7
        assert ns.diagonalization == "david"

    def test_validate_accepts_valid(self):
        ns = NumericalSettings(ecutwfc=60.0, ecutrho=480.0, conv_thr=1e-8, mixing_beta=0.4)
        ns.validate()  # Should not raise

    def test_validate_rejects_zero_ecutwfc(self):
        ns = NumericalSettings(ecutwfc=0.0, ecutrho=160.0)
        with pytest.raises(ValueError, match="ecutwfc"):
            ns.validate()

    def test_validate_rejects_negative_ecutrho(self):
        ns = NumericalSettings(ecutwfc=40.0, ecutrho=-1.0)
        with pytest.raises(ValueError, match="ecutrho"):
            ns.validate()

    def test_validate_rejects_ecutrho_smaller_than_ecutwfc(self):
        ns = NumericalSettings(ecutwfc=80.0, ecutrho=40.0)
        with pytest.raises(ValueError, match="ecutrho"):
            ns.validate()

    def test_validate_rejects_conv_thr_out_of_range(self):
        ns = NumericalSettings(conv_thr=2.0)
        with pytest.raises(ValueError, match="conv_thr"):
            ns.validate()

    def test_validate_rejects_mixing_beta_zero(self):
        ns = NumericalSettings(mixing_beta=0.0)
        with pytest.raises(ValueError, match="mixing_beta"):
            ns.validate()

    def test_validate_rejects_unknown_diagonalization(self):
        ns = NumericalSettings(diagonalization="magic")
        with pytest.raises(ValueError, match="diagonalization"):
            ns.validate()


# ---------------------------------------------------------------------------
# TestPhysicalOptions
# ---------------------------------------------------------------------------


class TestPhysicalOptions:
    def test_default_values(self):
        po = PhysicalOptions()
        assert po.occupations == "smearing"
        assert po.smearing == "gaussian"
        assert po.degauss == 0.01
        assert po.k_points_grid == [4, 4, 4]
        assert po.k_points_shift == [0, 0, 0]

    def test_validate_accepts_all_occupations_types(self):
        for occ in ("smearing", "fixed", "tetrahedra", "tetrahedra_lin", "tetrahedra_opt"):
            PhysicalOptions(occupations=occ).validate()

    def test_validate_rejects_unknown_occupations(self):
        with pytest.raises(ValueError, match="occupations"):
            PhysicalOptions(occupations="magic").validate()

    def test_validate_accepts_all_smearing_types(self):
        for smearing in ("gaussian", "methfessel-paxton", "marzari-vanderbilt", "fermi-dirac"):
            PhysicalOptions(smearing=smearing).validate()

    def test_validate_rejects_unknown_smearing(self):
        with pytest.raises(ValueError, match="smearing"):
            PhysicalOptions(smearing="magic").validate()

    def test_validate_rejects_negative_degauss(self):
        with pytest.raises(ValueError, match="degauss"):
            PhysicalOptions(degauss=-0.01).validate()

    def test_validate_rejects_k_grid_with_zeros(self):
        with pytest.raises(ValueError, match="k_points_grid"):
            PhysicalOptions(k_points_grid=[0, 4, 4]).validate()

    def test_validate_rejects_wrong_k_grid_length(self):
        with pytest.raises(ValueError, match="k_points_grid"):
            PhysicalOptions(k_points_grid=[4, 4]).validate()

    def test_validate_rejects_invalid_shift(self):
        with pytest.raises(ValueError, match="k_points_shift"):
            PhysicalOptions(k_points_shift=[0, 2, 0]).validate()


# ---------------------------------------------------------------------------
# TestCalculationContext
# ---------------------------------------------------------------------------


class TestCalculationContext:
    def _base_ctx(self, calc: str = "scf") -> CalculationContext:
        return CalculationContext(
            calculation=calc,
            numerical=NumericalSettings(),
            physical=PhysicalOptions(),
        )

    def test_validate_accepts_valid_scf(self):
        self._base_ctx("scf").validate()

    def test_validate_accepts_all_known_calculation_types(self):
        for calc in ("scf", "nscf", "relax", "vc-relax", "md", "vc-md"):
            self._base_ctx(calc).validate()

    def test_validate_rejects_unknown_calculation(self):
        with pytest.raises(ValueError, match="calculation"):
            self._base_ctx("phonon").validate()

    def test_is_frozen(self):
        ctx = self._base_ctx()
        with pytest.raises((AttributeError, TypeError)):
            ctx.calculation = "nscf"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestDefaultPolicy
# ---------------------------------------------------------------------------


class TestDefaultPolicy:
    def _classify(self, atoms: Atoms) -> StructureClassification:
        return classify_structure(atoms)

    def test_molecule_forces_gamma_only(self):
        h2 = _make_h2_molecule()
        cls = self._classify(h2)
        num = NumericalSettings()
        phys = PhysicalOptions(k_points_grid=[4, 4, 4])
        _, phys_out = apply_default_policy(cls, num, phys)
        assert phys_out.k_points_grid == [1, 1, 1]

    def test_metal_gets_methfessel_paxton(self):
        cls = StructureClassification(
            dimensionality=Dimensionality.D3,
            periodic_directions=(True, True, True),
            vacuum_directions=(),
            electronic_character=ElectronicCharacter.METAL,
            magnetic_character=MagneticCharacter.NONMAGNETIC,
            notes=[],
        )
        num = NumericalSettings()
        phys = PhysicalOptions()
        _, phys_out = apply_default_policy(cls, num, phys)
        assert phys_out.occupations == "smearing"
        assert phys_out.smearing == "methfessel-paxton"
        assert phys_out.degauss == 0.02

    def test_insulator_gets_fixed_occupations(self):
        cls = StructureClassification(
            dimensionality=Dimensionality.D3,
            periodic_directions=(True, True, True),
            vacuum_directions=(),
            electronic_character=ElectronicCharacter.INSULATOR,
            magnetic_character=MagneticCharacter.NONMAGNETIC,
            notes=[],
        )
        num = NumericalSettings()
        phys = PhysicalOptions()
        _, phys_out = apply_default_policy(cls, num, phys)
        assert phys_out.occupations == "fixed"

    def test_unknown_gets_smearing(self):
        cls = StructureClassification(
            dimensionality=Dimensionality.D3,
            periodic_directions=(True, True, True),
            vacuum_directions=(),
            electronic_character=ElectronicCharacter.UNKNOWN,
            magnetic_character=MagneticCharacter.UNKNOWN,
            notes=[],
        )
        num = NumericalSettings()
        # Pretend user manually set fixed, the policy should reset it to smearing as a safe default
        phys = PhysicalOptions(occupations="fixed")
        _, phys_out = apply_default_policy(cls, num, phys)
        assert phys_out.occupations == "smearing"

    def test_insulator_tightens_degauss(self):
        si = _make_bulk_silicon()
        cls = self._classify(si)
        assert cls.electronic_character == ElectronicCharacter.INSULATOR
        num = NumericalSettings()
        phys = PhysicalOptions(smearing="gaussian", degauss=0.01)
        _, phys_out = apply_default_policy(cls, num, phys)
        assert phys_out.degauss <= 0.01

    def test_already_non_gaussian_smearing_not_overridden(self):
        fe = _make_bulk_iron()
        cls = self._classify(fe)
        num = NumericalSettings()
        phys = PhysicalOptions(smearing="marzari-vanderbilt", degauss=0.01)
        _, phys_out = apply_default_policy(cls, num, phys)
        # Only overrides if user left "gaussian"
        assert phys_out.smearing == "marzari-vanderbilt"

    def test_returns_new_objects(self):
        si = _make_bulk_silicon()
        cls = self._classify(si)
        num = NumericalSettings()
        phys = PhysicalOptions()
        num_out, phys_out = apply_default_policy(cls, num, phys)
        # Original objects are not mutated
        assert phys.k_points_grid == [4, 4, 4]


# ---------------------------------------------------------------------------
# TestBuildQEInputNamelist
# ---------------------------------------------------------------------------


class TestBuildQEInputNamelist:
    def _ctx(self, calc: str) -> CalculationContext:
        return CalculationContext(
            calculation=calc,
            numerical=NumericalSettings(ecutwfc=60.0, ecutrho=480.0),
            physical=PhysicalOptions(smearing="methfessel-paxton", degauss=0.02),
        )

    def test_scf_has_required_namelists(self):
        result = build_qe_input_namelist(self._ctx("scf"))
        assert "control" in result
        assert "system" in result
        assert "electrons" in result
        assert "ions" not in result
        assert "cell" not in result

    def test_relax_has_ions(self):
        result = build_qe_input_namelist(self._ctx("relax"))
        assert "ions" in result
        assert "cell" not in result

    def test_vc_relax_has_ions_and_cell(self):
        result = build_qe_input_namelist(self._ctx("vc-relax"))
        assert "ions" in result
        assert "cell" in result

    def test_system_contains_cutoffs(self):
        result = build_qe_input_namelist(self._ctx("scf"))
        system = result["system"]
        assert system["ecutwfc"] == 60.0
        assert system["ecutrho"] == 480.0

    def test_system_contains_smearing_and_occupations(self):
        result = build_qe_input_namelist(self._ctx("scf"))
        system = result["system"]
        assert system["occupations"] == "smearing"
        assert system["smearing"] == "methfessel-paxton"
        assert system["degauss"] == 0.02

    def test_fixed_occupations_omits_smearing_and_degauss(self):
        ctx = CalculationContext(
            calculation="scf",
            numerical=NumericalSettings(),
            physical=PhysicalOptions(occupations="fixed"),
        )
        result = build_qe_input_namelist(ctx)
        system = result["system"]
        assert system["occupations"] == "fixed"
        assert "smearing" not in system
        assert "degauss" not in system

    def test_electrons_contains_conv_thr_and_mixing(self):
        ctx = CalculationContext(
            calculation="scf",
            numerical=NumericalSettings(conv_thr=1e-8, mixing_beta=0.4),
            physical=PhysicalOptions(),
        )
        result = build_qe_input_namelist(ctx)
        elec = result["electrons"]
        assert elec["conv_thr"] == 1e-8
        assert elec["mixing_beta"] == 0.4

    def test_control_contains_calculation(self):
        result = build_qe_input_namelist(self._ctx("relax"))
        assert result["control"]["calculation"] == "relax"

    def test_nscf_no_ions_or_cell(self):
        result = build_qe_input_namelist(self._ctx("nscf"))
        assert "ions" not in result
        assert "cell" not in result

    def test_returns_plain_dict(self):
        result = build_qe_input_namelist(self._ctx("scf"))
        assert isinstance(result, dict)
        for value in result.values():
            assert isinstance(value, dict)


# ---------------------------------------------------------------------------
# TestCleanQEInputText
# ---------------------------------------------------------------------------


class TestCleanQEInputText:
    _SAMPLE_SCF = """\
&CONTROL
   calculation = 'scf'
/

&SYSTEM
   ecutwfc = 60
   ecutrho = 480
/

&ELECTRONS
   conv_thr = 1.0e-6
/

&IONS
/

&CELL
/

ATOMIC_SPECIES
 Si  28.085  Si.pbe.upf

K_POINTS automatic
 8 8 8 0 0 0

ATOMIC_POSITIONS crystal
 Si  0.0  0.0  0.0
 Si  0.25 0.25 0.25
"""

    def test_removes_empty_ions_and_cell_for_scf(self):
        cleaned = clean_qe_input_text(self._SAMPLE_SCF, calc_type="scf")
        assert "&IONS" not in cleaned
        assert "&CELL" not in cleaned

    def test_preserves_required_namelists(self):
        cleaned = clean_qe_input_text(self._SAMPLE_SCF, calc_type="scf")
        assert "&CONTROL" in cleaned
        assert "&SYSTEM" in cleaned
        assert "&ELECTRONS" in cleaned

    def test_preserves_card_sections(self):
        cleaned = clean_qe_input_text(self._SAMPLE_SCF, calc_type="scf")
        assert "ATOMIC_SPECIES" in cleaned
        assert "ATOMIC_POSITIONS" in cleaned
        assert "K_POINTS" in cleaned

    def test_relax_keeps_ions(self):
        text = """\
&CONTROL
   calculation = 'relax'
/
&SYSTEM
   ecutwfc = 60
/
&ELECTRONS
   conv_thr = 1e-6
/
&IONS
/
ATOMIC_SPECIES
 Al 26.982 Al.pbe.upf
ATOMIC_POSITIONS crystal
 Al 0.0 0.0 0.0
"""
        cleaned = clean_qe_input_text(text, calc_type="relax")
        # &IONS is empty so it can be stripped — but required for relax
        assert "&IONS" in cleaned

    def test_vc_relax_keeps_cell(self):
        text = """\
&CONTROL
   calculation = 'vc-relax'
/
&SYSTEM
   ecutwfc = 60
/
&ELECTRONS
/
&IONS
/
&CELL
/
ATOMIC_SPECIES
 Fe 55.845 Fe.pbe.upf
ATOMIC_POSITIONS crystal
 Fe 0.0 0.0 0.0
"""
        cleaned = clean_qe_input_text(text, calc_type="vc-relax")
        assert "&CELL" in cleaned
        assert "&IONS" in cleaned

    def test_output_ends_with_newline(self):
        cleaned = clean_qe_input_text(self._SAMPLE_SCF, calc_type="scf")
        assert cleaned.endswith("\n")

    def test_no_triple_blank_lines(self):
        cleaned = clean_qe_input_text(self._SAMPLE_SCF, calc_type="scf")
        assert "\n\n\n" not in cleaned


# ---------------------------------------------------------------------------
# TestMakeDefaultContext
# ---------------------------------------------------------------------------


class TestMakeDefaultContext:
    def test_returns_calculation_context(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        assert isinstance(ctx, CalculationContext)

    def test_default_calculation_is_scf(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        assert ctx.calculation == "scf"

    def test_custom_calculation_type(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si, calculation="relax")
        assert ctx.calculation == "relax"

    def test_context_is_valid(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        ctx.validate()  # Should not raise

    def test_with_resolved_entries_uses_suggested_cutoffs(self):
        si = _make_bulk_silicon()
        # Use correct SSSP field names: cutoff_wfc / cutoff_rho
        entries = {"Si": {"cutoff_wfc": 55.0, "cutoff_rho": 440.0}}
        ctx = make_default_context(si, resolved_entries=entries)
        assert ctx.numerical.ecutwfc >= 55.0
        assert ctx.numerical.ecutrho >= 440.0

    def test_classification_is_attached(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        assert ctx.classification is not None
        assert isinstance(ctx.classification, StructureClassification)

    def test_policy_applied_for_metal(self):
        fe = _make_bulk_iron()
        ctx = make_default_context(fe, apply_policy=True)
        assert ctx.physical.smearing == "methfessel-paxton"

    def test_no_policy_keeps_gaussian(self):
        fe = _make_bulk_iron()
        ctx = make_default_context(fe, apply_policy=False)
        assert ctx.physical.smearing == "gaussian"

    def test_molecule_gets_gamma_grid_when_policy_applied(self):
        h2 = _make_h2_molecule()
        ctx = make_default_context(h2, apply_policy=True)
        assert ctx.physical.k_points_grid == [1, 1, 1]

    def test_custom_kpoint_spacing(self):
        si = _make_bulk_silicon()
        ctx_coarse = make_default_context(si, target_kpoint_spacing=0.20)
        ctx_fine = make_default_context(si, target_kpoint_spacing=0.05)
        assert all(
            f >= c
            for f, c in zip(
                ctx_fine.physical.k_points_grid,
                ctx_coarse.physical.k_points_grid,
            )
        )

    def test_dos_gets_finer_mesh_than_scf(self):
        si = _make_bulk_silicon()
        ctx_scf = make_default_context(si, calculation="scf")
        ctx_dos = make_default_context(si, calculation="dos")
        # Ensure DOS spacing (0.05) yields more points than SCF (0.15)
        assert all(
            d >= s
            for d, s in zip(
                ctx_dos.physical.k_points_grid,
                ctx_scf.physical.k_points_grid,
            )
        )
        assert ctx_dos.physical.k_points_grid != ctx_scf.physical.k_points_grid

    def test_fallback_cutoffs_without_entries(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si, resolved_entries=None)
        assert ctx.numerical.ecutwfc == FALLBACK_ECUTWFC_RY
        assert ctx.numerical.ecutrho == FALLBACK_ECUTRHO_RY

    # --- CutoffDecision attached to CalculationContext ---

    def test_cutoff_decision_is_attached(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        assert ctx.cutoff_decision is not None
        assert isinstance(ctx.cutoff_decision, CutoffDecision)

    def test_no_entries_labels_heuristic_fallback(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si, resolved_entries=None)
        assert ctx.cutoff_decision.source == "heuristic_fallback"

    def test_entries_label_sssp_metadata(self):
        si = _make_bulk_silicon()
        entries = {"Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0}}
        ctx = make_default_context(si, resolved_entries=entries)
        assert ctx.cutoff_decision.source == "sssp_metadata"

    def test_default_preset_on_decision_is_standard(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si)
        assert ctx.cutoff_decision.preset == CutoffPreset.STANDARD

    def test_publication_preset_tightens_conv_thr(self):
        si = _make_bulk_silicon()
        ctx = make_default_context(si, preset=CutoffPreset.PUBLICATION)
        assert ctx.numerical.conv_thr <= 1.0e-7  # publication uses 1e-8

    def test_quick_preset_lowers_cutoffs_vs_standard(self):
        si = _make_bulk_silicon()
        entries = {"Si": {"cutoff_wfc": 30.0, "cutoff_rho": 240.0}}
        ctx_q = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.QUICK)
        ctx_s = make_default_context(si, resolved_entries=entries, preset=CutoffPreset.STANDARD)
        assert ctx_q.numerical.ecutwfc <= ctx_s.numerical.ecutwfc
        assert ctx_q.numerical.ecutrho <= ctx_s.numerical.ecutrho
