import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
from ase.build import bulk
import streamlit as st

from qe_studio.core import structure_state as ss


class StructureStateTests(unittest.TestCase):
    def setUp(self) -> None:
        st.session_state.clear()

    def tearDown(self) -> None:
        st.session_state.clear()

    def test_structure_signature_is_order_independent(self) -> None:
        first = Atoms(
            "SiO2",
            scaled_positions=[[0.0, 0.0, 0.0], [0.25, 0.25, 0.25], [0.5, 0.5, 0.5]],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )
        second = Atoms(
            symbols=["O", "Si", "O"],
            scaled_positions=[[0.5, 0.5, 0.5], [0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )

        self.assertEqual(ss.get_structure_signature(first)["hash"], ss.get_structure_signature(second)["hash"])

    def test_structure_signature_wraps_equivalent_fractional_positions(self) -> None:
        first = Atoms(
            "Si2",
            scaled_positions=[[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )
        second = Atoms(
            "Si2",
            scaled_positions=[[1.0, 0.0, 0.0], [1.25, -0.75, 0.25]],
            cell=[5.0, 5.0, 5.0],
            pbc=True,
        )

        self.assertEqual(ss.get_structure_signature(first)["hash"], ss.get_structure_signature(second)["hash"])

    def test_atomic_positions_coordinate_mode_defaults_to_crystal(self) -> None:
        self.assertEqual(ss.get_atomic_positions_coordinate_mode(), "crystal")

    def test_commit_structure_source_marks_pseudopotentials_stale_for_same_elements(self) -> None:
        first = Atoms(
            "Si2",
            positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]],
            cell=[5.43, 5.43, 5.43],
            pbc=True,
        )
        second = Atoms(
            "Si2",
            positions=[[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]],
            cell=[5.43, 5.43, 5.43],
            pbc=True,
        )

        st.session_state[ss.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[ss.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[ss.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"element": "Si"}]
        st.session_state[ss.PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = ss.get_structure_signature(first)
        st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
            "resolved_settings": {"functional": "PBE", "mode": "efficiency", "version": "1.3.0"},
            "resolved_structure_signature": ss.get_structure_signature(first),
        }

        ss.commit_structure_source(second, source_kind="manual", source_label="Manual Builder")

        self.assertEqual(st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["state"], "stale")
        self.assertEqual(st.session_state[ss.SOURCE_KIND_STATE_KEY], "manual")
        self.assertIsNotNone(st.session_state[ss.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY])

    def test_commit_structure_source_resets_atomic_positions_coordinate_mode_to_crystal(self) -> None:
        atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.43, 5.43, 5.43], pbc=True)
        ss.set_atomic_positions_coordinate_mode("angstrom")

        ss.commit_structure_source(atoms, source_kind="manual", source_label="Manual Builder")

        self.assertEqual(ss.get_atomic_positions_coordinate_mode(), "crystal")

    def test_clear_structure_state_resets_atomic_positions_coordinate_mode_to_crystal(self) -> None:
        atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.43, 5.43, 5.43], pbc=True)
        ss.commit_structure_source(atoms, source_kind="manual", source_label="Manual Builder")
        ss.set_atomic_positions_coordinate_mode("angstrom")

        ss.clear_structure_state()

        self.assertEqual(ss.get_atomic_positions_coordinate_mode(), "crystal")

    def test_apply_structure_mode_selection_marks_pseudopotentials_stale_when_cell_changes(self) -> None:
        conventional = bulk("Si", "diamond", a=5.43, cubic=True)
        ss.commit_structure_source(conventional, source_kind="file", source_label="Si.cif", source_format="cif")
        source_signature = st.session_state[ss.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY]
        st.session_state[ss.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[ss.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[ss.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"element": "Si"}]
        st.session_state[ss.PSEUDO_STRUCTURE_SIGNATURE_STATE_KEY] = source_signature
        st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
            "resolved_settings": {"functional": "PBE", "mode": "efficiency", "version": "1.3.0"},
            "resolved_structure_signature": source_signature,
        }

        ss.apply_structure_mode_selection("primitive")

        self.assertEqual(st.session_state[ss.STRUCTURE_MODE_STATE_KEY], "primitive")
        self.assertEqual(st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["state"], "stale")

    def test_sync_pseudopotential_settings_state_marks_detailed_stale_message_on_mismatch(self) -> None:
        atoms = Atoms("Si2", positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        st.session_state[ss.ATOMS_STATE_KEY] = atoms
        st.session_state[ss.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[ss.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[ss.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"element": "Si"}]
        st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
            "resolved_settings": {"functional": "PBE", "mode": "efficiency", "version": "1.3.0", "fully_relativistic": False},
        }
        st.session_state[ss.PSEUDO_FUNCTIONAL_WIDGET_KEY] = "PBEsol"
        st.session_state[ss.PSEUDO_MODE_WIDGET_KEY] = "efficiency"
        st.session_state[ss.PSEUDO_VERSION_WIDGET_KEY] = "1.3.0"
        st.session_state[ss.PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY] = False

        ss.sync_pseudopotential_settings_state()

        self.assertEqual(st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["state"], "stale")
        self.assertIn("functional='PBEsol'", st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["message"])

    def test_sync_pseudopotential_settings_state_keeps_resolved_when_settings_match(self) -> None:
        atoms = Atoms("Si2", positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        st.session_state[ss.ATOMS_STATE_KEY] = atoms
        st.session_state[ss.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[ss.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[ss.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"element": "Si"}]
        st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
            "resolved_settings": {"functional": "PBE", "mode": "efficiency", "version": "1.3.0", "fully_relativistic": False},
        }
        st.session_state[ss.PSEUDO_FUNCTIONAL_WIDGET_KEY] = "PBE"
        st.session_state[ss.PSEUDO_MODE_WIDGET_KEY] = "efficiency"
        st.session_state[ss.PSEUDO_VERSION_WIDGET_KEY] = "1.3.0"
        st.session_state[ss.PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY] = False

        ss.sync_pseudopotential_settings_state()

        self.assertEqual(st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["state"], "resolved")

    def test_sync_pseudopotential_settings_state_treats_missing_fully_relativistic_as_false(self) -> None:
        atoms = Atoms("Si2", positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        st.session_state[ss.ATOMS_STATE_KEY] = atoms
        st.session_state[ss.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[ss.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[ss.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"element": "Si"}]
        st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
            "resolved_settings": {"functional": "PBE", "mode": "efficiency", "version": "1.3.0"},
        }
        st.session_state[ss.PSEUDO_FUNCTIONAL_WIDGET_KEY] = "PBE"
        st.session_state[ss.PSEUDO_MODE_WIDGET_KEY] = "efficiency"
        st.session_state[ss.PSEUDO_VERSION_WIDGET_KEY] = "1.3.0"
        st.session_state[ss.PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY] = False

        ss.sync_pseudopotential_settings_state()

        self.assertEqual(st.session_state[ss.PSEUDO_RESOLUTION_STATUS_STATE_KEY]["state"], "resolved")

    def test_generated_input_provenance_round_trip(self) -> None:
        atoms = Atoms("Si2", positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        ss.commit_structure_source(atoms, source_kind="manual", source_label="Manual Builder")

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            ss.record_generated_input_provenance(
                run_dir,
                ui_path=str(run_dir / "pw.in"),
                internal_path=str(run_dir / "espresso.pwi"),
                metadata_path=str(run_dir / "qe_input_metadata.json"),
            )
            record = ss.get_provenance_record(run_dir, ss.PROVENANCE_RECORD_INPUT)

        self.assertIsNotNone(record)
        self.assertEqual(record["structure_signature"]["hash"], st.session_state[ss.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY]["hash"])
        self.assertEqual(record["paths"]["metadata_path"], str(Path(tmp_dir) / "qe_input_metadata.json"))

    def test_generated_input_provenance_preserves_extra_metadata(self) -> None:
        atoms = Atoms("FeO", positions=[[0.0, 0.0, 0.0], [1.2, 1.2, 1.2]], cell=[4.3, 4.3, 4.3], pbc=True)
        ss.commit_structure_source(atoms, source_kind="manual", source_label="Manual Builder")

        dftu_metadata = {
            "enabled": True,
            "syntax": "legacy_system",
            "units": "eV",
            "method_warning": "Hubbard U values are method-dependent.",
            "species_order": ["Fe", "O"],
            "species_parameters": {
                "Fe": {"species": "Fe", "u_ev": 4.0, "source": "user_defined"},
            },
        }
        vdw_metadata = {
            "enabled": True,
            "method": "grimme-d3-zero",
            "backend": "semiempirical",
            "rendered_system_fields": {"vdw_corr": "grimme-d3", "dftd3_version": 3},
            "warning": "Using Quantum ESPRESSO defaults for optional D2/D3/TS/MBD tuning parameters in this phase.",
        }

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            ss.record_generated_input_provenance(
                run_dir,
                ui_path=str(run_dir / "pw.in"),
                internal_path=str(run_dir / "espresso.pwi"),
                extra={"dftu": dftu_metadata, "vdw": vdw_metadata},
            )
            record = ss.get_provenance_record(run_dir, ss.PROVENANCE_RECORD_INPUT)

        self.assertIsNotNone(record)
        self.assertEqual(record["extra"]["dftu"], dftu_metadata)
        self.assertEqual(record["extra"]["vdw"], vdw_metadata)

    def test_commit_structure_source_can_explicitly_reset_to_source_mode(self) -> None:
        atoms = bulk("Si", "diamond", a=5.43, cubic=True)
        st.session_state[ss.STRUCTURE_MODE_STATE_KEY] = "primitive"

        ss.commit_structure_source(
            atoms,
            source_kind="file",
            source_label="Si.cif",
            source_format="cif",
            requested_mode="source",
        )

        self.assertEqual(st.session_state[ss.STRUCTURE_MODE_STATE_KEY], "source")
        self.assertEqual(st.session_state[ss.ATOMS_STATE_KEY].get_chemical_formula(), atoms.get_chemical_formula())


if __name__ == "__main__":
    unittest.main()
