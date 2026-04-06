import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from ase import Atoms
import streamlit as st

import project_context as pc
import structure_state as ss


class ProjectContextTests(unittest.TestCase):
    def setUp(self) -> None:
        st.session_state.clear()

    def tearDown(self) -> None:
        st.session_state.clear()

    def test_build_project_context_without_structure_uses_empty_summaries(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            ctx = pc.build_project_context(Path(tmp_dir))

        self.assertFalse(ctx.structure.loaded)
        self.assertEqual(ctx.pseudopotentials.status_label, "Not resolved")
        self.assertFalse(ctx.input_files.available)
        self.assertFalse(ctx.artifacts.has_any_artifact)
        self.assertEqual(ctx.jobs.latest_status_label, "Idle")

    def test_build_project_context_with_structure_populates_structure_summary(self) -> None:
        atoms = Atoms("Si2", positions=[[0, 0, 0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
        st.session_state[pc.ATOMS_STATE_KEY] = atoms
        st.session_state[pc.RAW_ATOMS_STATE_KEY] = atoms.copy()
        st.session_state[pc.FILENAME_STATE_KEY] = "Si.cif"
        st.session_state[pc.SOURCE_KIND_STATE_KEY] = "file"
        st.session_state[pc.SOURCE_LABEL_STATE_KEY] = "Si.cif"
        st.session_state[pc.SOURCE_FORMAT_STATE_KEY] = "cif"
        st.session_state[pc.STRUCTURE_MODE_STATE_KEY] = "primitive"
        st.session_state[pc.STRUCTURE_MODE_STATUS_STATE_KEY] = "Using the primitive cell."

        with TemporaryDirectory() as tmp_dir:
            ctx = pc.build_project_context(Path(tmp_dir))

        self.assertTrue(ctx.structure.loaded)
        self.assertEqual(ctx.structure.filename, "Si.cif")
        self.assertEqual(ctx.structure.source_kind, "file")
        self.assertEqual(ctx.structure.source_label, "Si.cif")
        self.assertEqual(ctx.structure.cell_mode_label, "Primitive Cell")
        self.assertEqual(ctx.structure.atom_count, 2)
        self.assertIn("a =", ctx.structure.cell_parameters or "")

    def test_build_project_context_reports_resolved_and_stale_pseudopotential_states(self) -> None:
        st.session_state[pc.PSEUDO_DIR_STATE_KEY] = "qe_runs/pseudopotentials"
        st.session_state[pc.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
        st.session_state[pc.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"Element": "Si"}]
        st.session_state[pc.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "resolved",
            "message": "Resolved pseudopotentials.",
            "warnings": [],
        }

        with TemporaryDirectory() as tmp_dir:
            resolved_ctx = pc.build_project_context(Path(tmp_dir))

        self.assertTrue(resolved_ctx.pseudopotentials.ready)
        self.assertEqual(resolved_ctx.pseudopotentials.status_label, "Resolved")

        st.session_state[pc.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {
            "state": "stale",
            "message": "Refresh required.",
            "warnings": ["Changed structure."],
        }
        with TemporaryDirectory() as tmp_dir:
            stale_ctx = pc.build_project_context(Path(tmp_dir))

        self.assertFalse(stale_ctx.pseudopotentials.ready)
        self.assertEqual(stale_ctx.pseudopotentials.status_label, "Stale")
        self.assertEqual(stale_ctx.pseudopotentials.warning_count, 1)

    def test_build_project_context_prefers_session_preview_and_falls_back_to_disk(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            st.session_state[pc.QE_INPUT_TEXT_STATE_KEY] = "&CONTROL\n/\n"
            st.session_state[pc.QE_INPUT_UI_PATH_STATE_KEY] = str(run_dir / "pw.in")

            session_ctx = pc.build_project_context(run_dir)
            self.assertTrue(session_ctx.input_files.available)
            self.assertEqual(session_ctx.input_files.preview_source, "session")

            st.session_state.pop(pc.QE_INPUT_TEXT_STATE_KEY, None)
            (run_dir / "pw.in").write_text("&CONTROL\n/\n", encoding="utf-8")

            disk_ctx = pc.build_project_context(run_dir)
            self.assertTrue(disk_ctx.input_files.available)
            self.assertEqual(disk_ctx.input_files.preview_source, "disk")

    def test_build_project_context_reports_out_of_sync_input_provenance(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            current_atoms = Atoms("Si2", positions=[[0, 0, 0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
            stale_atoms = Atoms("Si2", positions=[[0, 0, 0], [2.0, 2.0, 2.0]], cell=[5.43, 5.43, 5.43], pbc=True)
            st.session_state[pc.ATOMS_STATE_KEY] = current_atoms
            st.session_state[pc.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY] = ss.get_structure_signature(current_atoms)
            (run_dir / "pw.in").write_text("&CONTROL\n/\n", encoding="utf-8")
            ss.update_provenance_record(
                run_dir,
                ss.PROVENANCE_RECORD_INPUT,
                structure_signature=ss.get_structure_signature(stale_atoms),
                paths={"ui_path": str(run_dir / "pw.in")},
            )

            ctx = pc.build_project_context(run_dir)

        self.assertEqual(ctx.input_files.provenance_state, "out_of_sync")
        self.assertIn("different active structure", ctx.input_files.provenance_message or "")

    def test_build_project_context_discovers_lightweight_artifact_presence(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            (run_dir / "pw.in").write_text("&CONTROL\n/\n", encoding="utf-8")
            (run_dir / "sample.dos").write_text("0.0 1.0\n1.0 2.0\n", encoding="utf-8")
            (run_dir / "filband.gnu").write_text("0.0 1.0\n1.0 2.0\n\n0.0 3.0\n1.0 4.0\n", encoding="utf-8")

            ctx = pc.build_project_context(run_dir)

        self.assertTrue(ctx.artifacts.has_any_artifact)
        self.assertTrue(ctx.artifacts.dos_detected)
        self.assertTrue(ctx.artifacts.bands_detected)
        self.assertEqual(ctx.artifacts.band_file, run_dir / "filband.gnu")

    def test_build_project_context_reports_unknown_artifact_provenance_without_records(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            atoms = Atoms("Si2", positions=[[0, 0, 0], [1.35, 1.35, 1.35]], cell=[5.43, 5.43, 5.43], pbc=True)
            st.session_state[pc.ATOMS_STATE_KEY] = atoms
            st.session_state[pc.ACTIVE_STRUCTURE_SIGNATURE_STATE_KEY] = ss.get_structure_signature(atoms)
            (run_dir / "pw.in").write_text("&CONTROL\n/\n", encoding="utf-8")
            (run_dir / "pw.out").write_text("pw output\n", encoding="utf-8")

            ctx = pc.build_project_context(run_dir)

        self.assertEqual(ctx.artifacts.provenance_state, "unknown")
        self.assertIn("provenance is unavailable", ctx.artifacts.provenance_message or "")

    def test_build_project_context_reconstructs_active_job_summary(self) -> None:
        st.session_state[pc.RUN_ACTIVE_STATE_KEY] = True
        st.session_state[pc.RUN_JOB_OWNER_STATE_KEY] = pc.JOB_OWNER_DOS
        st.session_state[pc.RUN_JOB_LABEL_STATE_KEY] = "Density of States (DOS)"
        st.session_state[pc.RUN_PHASE_LABEL_STATE_KEY] = "dos.x"
        st.session_state[pc.RUN_RETURNCODE_STATE_KEY] = None
        st.session_state[pc.RUN_TAIL_LINES_STATE_KEY] = ["line 1\n"]

        with TemporaryDirectory() as tmp_dir:
            ctx = pc.build_project_context(Path(tmp_dir))

        self.assertEqual(ctx.jobs.latest_status_label, "Running")
        self.assertIn("Density of States", ctx.jobs.latest_status_detail or "")
        self.assertEqual(pc.get_job_summary(pc.JOB_OWNER_DOS)["status"], "running")

    def test_build_project_context_does_not_call_heavy_dos_or_band_parsers(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            with mock.patch("electronic_properties.parse_qe_dos", side_effect=AssertionError("heavy DOS parse called")), mock.patch(
                "electronic_properties.parse_qe_band_structure",
                side_effect=AssertionError("heavy band parse called"),
            ):
                ctx = pc.build_project_context(run_dir)

        self.assertFalse(ctx.artifacts.has_any_artifact)


if __name__ == "__main__":
    unittest.main()
