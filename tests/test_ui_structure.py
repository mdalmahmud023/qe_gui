import unittest

import streamlit as st

import ui_structure as ui


class UIStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        st.session_state.clear()

    def tearDown(self) -> None:
        st.session_state.clear()

    def test_reset_manual_builder_draft_increments_nonce(self) -> None:
        original_key = ui._draft_widget_key(ui.MANUAL_FORMULA_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY)

        ui._reset_manual_builder_draft()

        updated_key = ui._draft_widget_key(ui.MANUAL_FORMULA_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY)
        self.assertNotEqual(original_key, updated_key)
        self.assertEqual(st.session_state[ui.MANUAL_DRAFT_NONCE_STATE_KEY], 1)

    def test_reset_prototype_builder_draft_increments_nonce(self) -> None:
        original_key = ui._draft_widget_key(ui.PROTOTYPE_A_SITE_WIDGET_KEY, ui.PROTOTYPE_DRAFT_NONCE_STATE_KEY)

        ui._reset_prototype_builder_draft()

        updated_key = ui._draft_widget_key(ui.PROTOTYPE_A_SITE_WIDGET_KEY, ui.PROTOTYPE_DRAFT_NONCE_STATE_KEY)
        self.assertNotEqual(original_key, updated_key)
        self.assertEqual(st.session_state[ui.PROTOTYPE_DRAFT_NONCE_STATE_KEY], 1)

    def test_reset_file_import_draft_increments_nonce(self) -> None:
        original_key = ui._draft_widget_key(ui.FILE_FORMAT_WIDGET_KEY, ui.FILE_DRAFT_NONCE_STATE_KEY)

        ui._reset_file_import_draft()

        updated_key = ui._draft_widget_key(ui.FILE_FORMAT_WIDGET_KEY, ui.FILE_DRAFT_NONCE_STATE_KEY)
        self.assertNotEqual(original_key, updated_key)
        self.assertEqual(st.session_state[ui.FILE_DRAFT_NONCE_STATE_KEY], 1)


if __name__ == "__main__":
    unittest.main()
