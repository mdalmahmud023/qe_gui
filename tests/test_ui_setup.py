import unittest
from types import SimpleNamespace
from unittest import mock

import streamlit as st

from qe_studio.pages import ui_setup


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


if __name__ == "__main__":
    unittest.main()
