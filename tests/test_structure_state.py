import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
from ase.build import bulk
import streamlit as st

import structure_state as ss


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

    def test_generated_input_provenance_round_trip(self) -> None:
        atoms = Atoms("Si2", positions=[[0.0, 0.0, 0.0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        ss.commit_structure_source(atoms, source_kind="manual", source_label="Manual Builder")

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            ss.record_generated_input_provenance(run_dir, ui_path=str(run_dir / "pw.in"), internal_path=str(run_dir / "espresso.pwi"))
            record = ss.get_provenance_record(run_dir, ss.PROVENANCE_RECORD_INPUT)

        self.assertIsNotNone(record)
        self.assertEqual(record["structure_signature"]["hash"], st.session_state[ss.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY]["hash"])

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
