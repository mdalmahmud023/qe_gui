import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

import streamlit as st

from qe_studio.pages import ui_structure as ui


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
        family_key = "ordered_double_perovskite_a2bbx6"
        original_key = ui._prototype_family_field_widget_key(family_key, "a_site")

        ui._reset_prototype_builder_draft(family_key)

        updated_key = ui._prototype_family_field_widget_key(family_key, "a_site")
        self.assertNotEqual(original_key, updated_key)
        self.assertEqual(st.session_state[ui._prototype_family_nonce_state_key(family_key)], 1)

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

    def test_build_source_detail_rows_skips_hidden_internal_keys(self) -> None:
        rows = ui._build_source_detail_rows(
            {
                "_prototype_family_key": "ordered_double_perovskite_a2bbx6",
                "prototype_family": "Ordered Double Perovskite",
                "formula_template": "A2BB'X6",
            }
        )

        self.assertEqual(
            rows,
            [
                {"Field": "Prototype Family", "Value": "Ordered Double Perovskite"},
                {"Field": "Formula Template", "Value": "A2BB'X6"},
            ],
        )

    def test_prototype_family_widget_keys_are_scoped_per_family(self) -> None:
        first_key = ui._prototype_family_field_widget_key("ordered_double_perovskite_a2bbx6", "a_site")
        second_key = ui._prototype_family_field_widget_key("cubic_perovskite_abx3", "a_site")

        self.assertNotEqual(first_key, second_key)

    def test_resetting_one_prototype_family_draft_does_not_touch_another(self) -> None:
        first_family = "ordered_double_perovskite_a2bbx6"
        second_family = "cubic_perovskite_abx3"

        ui._reset_prototype_builder_draft(first_family)

        self.assertEqual(st.session_state[ui._prototype_family_nonce_state_key(first_family)], 1)
        self.assertNotIn(ui._prototype_family_nonce_state_key(second_family), st.session_state)

    def test_render_structure_mode_controls_defaults_atomic_positions_toggle_to_fractional(self) -> None:
        atoms = SimpleNamespace()
        ctx = SimpleNamespace(
            raw_atoms=atoms,
            structure=SimpleNamespace(mode_status=None),
        )
        observed: dict[str, object] = {}

        def fake_selectbox(_label, options, *, index=0, **_kwargs):
            return options[index]

        def fake_radio(label, options, *, index=0, horizontal=False, format_func=None, **_kwargs):
            observed["label"] = label
            observed["options"] = options
            observed["index"] = index
            observed["horizontal"] = horizontal
            observed["labels"] = [format_func(option) for option in options] if format_func is not None else list(options)
            return options[index]

        with (
            mock.patch.object(ui.st, "markdown"),
            mock.patch.object(ui.st, "info"),
            mock.patch.object(ui.st, "warning"),
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "selectbox", side_effect=fake_selectbox),
            mock.patch.object(ui.st, "radio", side_effect=fake_radio),
            mock.patch.object(ui, "get_structure_mode", return_value="source"),
        ):
            ui._render_structure_mode_controls(ctx)

        self.assertEqual(observed["label"], "Atomic positions coordinate system")
        self.assertEqual(observed["options"], ui.ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS)
        self.assertEqual(observed["index"], 0)
        self.assertTrue(observed["horizontal"])
        self.assertEqual(
            observed["labels"],
            ["Fractional (crystal)", "Cartesian (angstrom)"],
        )


if __name__ == "__main__":
    unittest.main()
