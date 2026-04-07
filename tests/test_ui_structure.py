import unittest
from contextlib import nullcontext
from unittest import mock

import streamlit as st

import ui_structure as ui


class _FakeColumn:
    def number_input(self, *_args, **_kwargs) -> None:
        return None

    def form_submit_button(self, *_args, **_kwargs) -> bool:
        return False


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

    def test_manual_builder_does_not_seed_data_editor_widget_key_with_rows(self) -> None:
        observed: dict[str, object] = {}

        def fake_columns(spec, **_kwargs):
            if isinstance(spec, int):
                count = spec
            else:
                count = len(spec)
            return [_FakeColumn() for _ in range(count)]

        def fake_data_editor(data, *, key, **_kwargs):
            observed["data"] = data
            observed["key"] = key
            observed["session_value"] = st.session_state.get(key)
            return data

        with (
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "form", return_value=nullcontext()),
            mock.patch.object(ui.st, "text_input", return_value=""),
            mock.patch.object(ui.st, "columns", side_effect=fake_columns),
            mock.patch.object(ui.st, "radio", return_value=ui.MANUAL_COORDINATE_MODE_FRACTIONAL),
            mock.patch.object(ui.st, "data_editor", side_effect=fake_data_editor),
            mock.patch.object(ui.st, "rerun"),
        ):
            ui._render_manual_builder_mode()

        expected_key = ui._draft_widget_key(ui.MANUAL_ATOM_TABLE_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY)
        self.assertEqual(observed["key"], expected_key)
        self.assertEqual(observed["data"], ui._default_manual_atom_rows())
        self.assertIsNone(observed["session_value"])
        self.assertNotIn(expected_key, st.session_state)


if __name__ == "__main__":
    unittest.main()
