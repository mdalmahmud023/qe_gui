import unittest
from types import SimpleNamespace
from unittest import mock

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

    def test_sanitize_saved_qe_parameters_disables_empty_dftu_selection(self) -> None:
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {},
            }
        )

        self.assertFalse(sanitized["dftu_enabled"])
        self.assertEqual(sanitized["hubbard_u"], {})

    def test_sanitize_saved_qe_parameters_normalizes_invalid_vdw_method(self) -> None:
        sanitized = ui_setup._sanitize_saved_qe_parameters({"vdw_method": "magic"})

        self.assertEqual(sanitized["vdw_method"], VdwMethod.NONE.value)

    def test_sanitize_saved_qe_parameters_drops_invalid_u_values(self) -> None:
        sanitized = ui_setup._sanitize_saved_qe_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {
                    "Au": {"species": "Au", "u_ev": 0.0},
                    "O": {"species": "O", "u_ev": 4.5},
                },
            }
        )

        self.assertTrue(sanitized["dftu_enabled"])
        self.assertEqual(list(sanitized["hubbard_u"]), ["O"])
        self.assertEqual(sanitized["hubbard_u"]["O"]["u_ev"], 4.5)

    def test_sanitize_saved_qe_parameters_disables_incompatible_soc(self) -> None:
        with mock.patch(
            "qe_studio.pages.ui_setup.inspect_soc_compatibility",
            return_value=SimpleNamespace(all_compatible=False),
        ):
            sanitized = ui_setup._sanitize_saved_qe_parameters(
                {
                    "noncolin": True,
                    "lspinorb": True,
                },
                resolved_entries={"Au": {"filename": "Au.upf"}},
            )

        self.assertFalse(sanitized["noncolin"])
        self.assertFalse(sanitized["lspinorb"])

    def test_validate_qe_submission_parameters_rejects_empty_dftu_selection(self) -> None:
        message = ui_setup._validate_qe_submission_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {},
            }
        )

        self.assertIn("no Hubbard U species", message or "")

    def test_validate_qe_submission_parameters_rejects_nonpositive_u(self) -> None:
        message = ui_setup._validate_qe_submission_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {
                    "Au": {"species": "Au", "u_ev": 0.0},
                },
            }
        )

        self.assertIn("greater than 0 eV", message or "")

    def test_validate_qe_submission_parameters_accepts_positive_u(self) -> None:
        message = ui_setup._validate_qe_submission_parameters(
            {
                "dftu_enabled": True,
                "hubbard_u": {
                    "Au": {"species": "Au", "u_ev": 3.2},
                },
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

    def test_build_generation_context_from_parameters_attaches_reproducibility_fields(self) -> None:
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
                "ecutwfc": 60.0,
                "ecutrho": 480.0,
                "occupations": "smearing",
                "smearing": "gaussian",
                "degauss": 0.01,
                "k_points_grid": [4, 4, 4],
                "conv_thr": 1.0e-6,
                "mixing_beta": 0.7,
                "nspin": 2,
                "starting_magnetization": {"Fe": 0.5},
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
        self.assertIn("Fe", context.resolved_entries)


if __name__ == "__main__":
    unittest.main()
