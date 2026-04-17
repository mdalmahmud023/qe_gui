import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ase import Atoms
import streamlit as st

from qe_studio.pages import ui_setup
from qe_studio.services.input_generator import VdwMethod


class UISetupHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        st.session_state.clear()

    def tearDown(self) -> None:
        st.session_state.clear()

    def test_queue_and_apply_pseudopotential_widget_sync_sets_widget_state(self) -> None:
        ui_setup._queue_pseudopotential_widget_sync(
            {
                "functional": "PBEsol",
                "mode": "precision",
                "version": "1.3.0",
                "fully_relativistic": True,
            }
        )

        ui_setup._apply_pending_pseudopotential_widget_sync()

        self.assertEqual(st.session_state["pseudo_widget_functional"], "PBEsol")
        self.assertEqual(st.session_state["pseudo_widget_mode"], "precision")
        self.assertEqual(st.session_state["pseudo_widget_version"], "1.3.0")
        self.assertTrue(st.session_state["pseudo_widget_fully_relativistic"])

    def test_sanitize_saved_qe_parameters_keeps_requested_empty_dftu_selection(self) -> None:
        atoms = Atoms("FeO", positions=[[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]], cell=[4.3, 4.3, 4.3], pbc=True)
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {},
            },
            atoms=atoms,
        )

        self.assertTrue(sanitized["dftu_enabled"])
        self.assertEqual(sanitized["hubbard_u"], {})

    def test_sanitize_saved_qe_parameters_normalizes_invalid_vdw_method(self) -> None:
        sanitized = ui_setup._sanitize_saved_qe_parameters({"vdw_method": "magic"})

        self.assertEqual(sanitized["vdw_method"], VdwMethod.NONE.value)

    def test_sanitize_saved_qe_parameters_preserves_zero_u_values_for_requested_state(self) -> None:
        atoms = Atoms("AuO", positions=[[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]], cell=[4.3, 4.3, 4.3], pbc=True)
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {
                    "Au": {"species": "Au", "u_ev": 0.0},
                    "O": {"species": "O", "u_ev": 4.5},
                },
            },
            atoms=atoms,
        )

        self.assertTrue(sanitized["dftu_enabled"])
        self.assertEqual(set(sanitized["hubbard_u"]), {"Au", "O"})
        self.assertEqual(sanitized["hubbard_u"]["Au"]["u_ev"], 0.0)
        self.assertEqual(sanitized["hubbard_u"]["O"]["u_ev"], 4.5)

    def test_sanitize_saved_qe_parameters_preserves_requested_soc(self) -> None:
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "noncolin": True,
                "lspinorb": True,
            },
        )

        self.assertTrue(sanitized["noncolin"])
        self.assertTrue(sanitized["lspinorb"])

    def test_sanitize_saved_qe_parameters_recomputes_auto_control_defaults(self) -> None:
        atoms = Atoms("Fe2", positions=[[0.0, 0.0, 0.0], [1.4, 1.4, 1.4]], cell=[4.3, 4.3, 4.3], pbc=True)
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "prefix": "old_prefix",
                "prefix_source": "auto",
                "outdir": ".",
                "outdir_source": "auto",
            },
            atoms=atoms,
        )

        self.assertEqual(sanitized["prefix"], "fe2")
        self.assertEqual(sanitized["outdir"], "./tmp")

    def test_sanitize_saved_qe_parameters_prunes_removed_species_settings(self) -> None:
        atoms = Atoms("Fe", positions=[[0.0, 0.0, 0.0]], cell=[4.3, 4.3, 4.3], pbc=True)
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "spin_source": "user",
                "starting_magnetization": {
                    "Fe": {"species": "Fe", "value": 0.8, "source": "user_defined"},
                    "O": {"species": "O", "value": 0.1, "source": "user_defined"},
                },
                "dftu_enabled": True,
                "hubbard_u": {
                    "Fe": {"species": "Fe", "u_ev": 4.0, "source": "user_defined"},
                    "O": {"species": "O", "u_ev": 3.0, "source": "user_defined"},
                },
            },
            atoms=atoms,
        )

        self.assertEqual(set(sanitized["starting_magnetization"]), {"Fe"})
        self.assertEqual(set(sanitized["hubbard_u"]), {"Fe"})
        self.assertTrue(sanitized["spin_state_pruned"])
        self.assertTrue(sanitized["hubbard_u_pruned"])

    def test_validate_qe_submission_parameters_accepts_empty_dftu_selection(self) -> None:
        message = ui_setup._validate_qe_submission_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {},
            }
        )

        self.assertIsNone(message)

    def test_get_generation_submit_block_reason_prefers_active_run_message(self) -> None:
        message = ui_setup._get_generation_submit_block_reason(
            blocker_message="Resolve pseudopotentials first.",
            run_active=True,
        )

        self.assertIn("active calculation", message or "")

    def test_get_generation_submit_block_reason_uses_blocker_message(self) -> None:
        message = ui_setup._get_generation_submit_block_reason(
            blocker_message="Resolve pseudopotentials first.",
            run_active=False,
        )

        self.assertEqual(message, "Resolve pseudopotentials first.")

    def test_build_vdw_settings_from_parameters_uses_selected_method(self) -> None:
        settings = ui_setup._build_vdw_settings_from_parameters({"vdw_method": VdwMethod.D3_ZERO.value})

        self.assertEqual(settings.normalized_method, VdwMethod.D3_ZERO)

    def test_build_generation_context_from_parameters_attaches_control_and_feature_states(self) -> None:
        atoms = Atoms(
            "FeO",
            positions=[[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]],
            cell=[4.3, 4.3, 4.3],
            pbc=True,
        )
        context = ui_setup._build_generation_context_from_parameters(
            atoms,
            {
                "calculation": "scf",
                "prefix": "FeO Project!",
                "outdir": "./tmp",
                "ecutwfc": 60.0,
                "ecutrho": 480.0,
                "occupations": "smearing",
                "smearing": "gaussian",
                "degauss": 0.01,
                "k_points_grid": [4, 4, 4],
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
            resolved_entries={
                "Fe": {"filename": "Fe.test.UPF", "cutoff_wfc": 60.0, "cutoff_rho": 480.0},
                "O": {"filename": "O.test.UPF", "cutoff_wfc": 50.0, "cutoff_rho": 400.0},
            },
        )

        self.assertIsNotNone(context.classification)
        self.assertIsNotNone(context.cutoff_decision)
        self.assertEqual(context.cutoff_decision.source, "sssp_metadata")
        self.assertEqual(context.species_order, ("Fe", "O"))
        self.assertTrue(context.hubbard_u.enabled)
        self.assertEqual(context.vdw.normalized_method, VdwMethod.D3_ZERO)
        self.assertEqual(context.control.prefix, "feo_project")
        self.assertEqual(context.control.outdir, "./tmp")
        self.assertEqual(context.feature_states["control"].status, "adjusted")
        self.assertIn("sanitized_prefix", [reason.code for reason in context.feature_states["control"].reasons])

    def test_generate_and_persist_qe_input_blocked_attempt_does_not_overwrite_existing_files(self) -> None:
        atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.4, 5.4, 5.4], pbc=True)

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            existing_pw = run_dir / "pw.in"
            existing_internal = run_dir / "espresso.pwi"
            existing_metadata = run_dir / "qe_input_metadata.json"
            existing_pw.write_text("old pw input\n", encoding="utf-8")
            existing_internal.write_text("old internal input\n", encoding="utf-8")
            existing_metadata.write_text('{"old": true}\n', encoding="utf-8")

            result = ui_setup._generate_and_persist_qe_input(
                atoms,
                {
                    "calculation": "scf",
                    "prefix": "si",
                    "outdir": "./tmp",
                    "ecutwfc": 40.0,
                    "ecutrho": 160.0,
                    "occupations": "fixed",
                    "smearing": "gaussian",
                    "degauss": 0.0,
                    "k_points_grid": [4, 4, 4],
                    "conv_thr": 1.0e-6,
                    "mixing_beta": 0.7,
                    "nspin": 1,
                    "starting_magnetization": {},
                    "noncolin": True,
                    "lspinorb": True,
                    "dftu_enabled": False,
                    "hubbard_u": {},
                    "vdw_method": VdwMethod.NONE.value,
                },
                {"Si": "Si.UPF"},
                str(run_dir),
                run_dir=run_dir,
                current_pseudo_settings={
                    "functional": "PBE",
                    "mode": "efficiency",
                    "version": "1.3.0",
                    "fully_relativistic": False,
                },
                resolved_entries=None,
                source_metadata={"source_kind": "manual"},
                structure_signature={"hash": "abc"},
            )

            self.assertFalse(result.success)
            self.assertTrue(any(issue.level == "error" for issue in result.validation_issues))
            self.assertEqual(existing_pw.read_text(encoding="utf-8"), "old pw input\n")
            self.assertEqual(existing_internal.read_text(encoding="utf-8"), "old internal input\n")
            self.assertEqual(existing_metadata.read_text(encoding="utf-8"), '{"old": true}\n')

    def test_generate_and_persist_qe_input_success_writes_sanitized_control_and_metadata(self) -> None:
        atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.4, 5.4, 5.4], pbc=True)

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            pseudo_dir = run_dir / "pseudos"
            pseudo_dir.mkdir()

            result = ui_setup._generate_and_persist_qe_input(
                atoms,
                {
                    "calculation": "scf",
                    "prefix": "Si Project With Spaces",
                    "outdir": "./tmp",
                    "ecutwfc": 40.0,
                    "ecutrho": 160.0,
                    "occupations": "fixed",
                    "smearing": "gaussian",
                    "degauss": 0.0,
                    "k_points_grid": [4, 4, 4],
                    "conv_thr": 1.0e-6,
                    "mixing_beta": 0.7,
                    "nspin": 1,
                    "starting_magnetization": {},
                    "noncolin": False,
                    "lspinorb": False,
                    "dftu_enabled": False,
                    "hubbard_u": {},
                    "vdw_method": VdwMethod.NONE.value,
                },
                {"Si": "Si.UPF"},
                str(pseudo_dir),
                run_dir=run_dir,
                current_pseudo_settings={
                    "functional": "PBE",
                    "mode": "efficiency",
                    "version": "1.3.0",
                    "fully_relativistic": False,
                },
                resolved_entries={"Si": {"filename": "Si.UPF", "cutoff_wfc": 40.0, "cutoff_rho": 160.0}},
                source_metadata={"source_kind": "manual"},
                structure_signature={"hash": "si-success"},
            )

            self.assertTrue(result.success)
            self.assertIsNotNone(result.ui_path)
            self.assertIsNotNone(result.metadata_path)
            assert result.ui_path is not None
            assert result.metadata_path is not None
            rendered_text = result.ui_path.read_text(encoding="utf-8")
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

            self.assertIn("'si_project_with_spaces'", rendered_text)
            self.assertIn("prefix", rendered_text)
            self.assertIn("'./tmp'", rendered_text)
            self.assertIn("outdir", rendered_text)
            self.assertEqual(metadata["control"]["requested_prefix"], "Si Project With Spaces")
            self.assertEqual(metadata["control"]["rendered_prefix"], "si_project_with_spaces")
            self.assertEqual(metadata["control"]["rendered_outdir"], "./tmp")
            self.assertEqual(Path(metadata["control"]["resolved_outdir"]), run_dir / "tmp")

    def test_generate_and_persist_qe_input_blocked_vdw_conflict_keeps_existing_preview_and_files(self) -> None:
        atoms = Atoms("Si", positions=[[0.0, 0.0, 0.0]], cell=[5.4, 5.4, 5.4], pbc=True)

        with TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir)
            existing_pw = run_dir / "pw.in"
            existing_internal = run_dir / "espresso.pwi"
            existing_metadata = run_dir / "qe_input_metadata.json"
            existing_pw.write_text("old pw input\n", encoding="utf-8")
            existing_internal.write_text("old internal input\n", encoding="utf-8")
            existing_metadata.write_text('{"old": true}\n', encoding="utf-8")
            st.session_state[ui_setup.QE_INPUT_TEXT_STATE_KEY] = "existing preview"

            result = ui_setup._generate_and_persist_qe_input(
                atoms,
                {
                    "calculation": "scf",
                    "prefix": "si",
                    "outdir": "./tmp",
                    "ecutwfc": 40.0,
                    "ecutrho": 160.0,
                    "occupations": "fixed",
                    "smearing": "gaussian",
                    "degauss": 0.0,
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
                    "vdw_state": {"vdw_corr": "grimme-d3", "input_dft": "vdW-DF2"},
                },
                {"Si": "Si.UPF"},
                str(run_dir),
                run_dir=run_dir,
                current_pseudo_settings={
                    "functional": "PBE",
                    "mode": "efficiency",
                    "version": "1.3.0",
                    "fully_relativistic": False,
                },
                resolved_entries={"Si": {"filename": "Si.UPF", "cutoff_wfc": 40.0, "cutoff_rho": 160.0}},
                source_metadata={"source_kind": "manual"},
                structure_signature={"hash": "si-vdw-conflict"},
            )

            self.assertFalse(result.success)
            self.assertTrue(any(issue.code == "vdw_conflicting_state" for issue in result.validation_issues))
            self.assertEqual(existing_pw.read_text(encoding="utf-8"), "old pw input\n")
            self.assertEqual(existing_internal.read_text(encoding="utf-8"), "old internal input\n")
            self.assertEqual(existing_metadata.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(st.session_state[ui_setup.QE_INPUT_TEXT_STATE_KEY], "existing preview")


if __name__ == "__main__":
    unittest.main()
