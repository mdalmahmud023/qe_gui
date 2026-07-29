import unittest
from types import SimpleNamespace
from unittest import mock

import streamlit as st

from qe_studio.core import structure_state
from qe_studio.pages import ui_structure as ui


class _FakeColumn:
    def __init__(self, *, button_clicked: bool = False) -> None:
        self.button_clicked = button_clicked

    def number_input(self, *_args, **kwargs):
        return kwargs.get("value")

    def button(self, *_args, **_kwargs) -> bool:
        return self.button_clicked


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
        saved_rows = [{"symbol": "Si", "x": 0.25, "y": 0.25, "z": 0.25}]
        drafts = ui._default_structure_drafts()
        drafts["manual"]["formula_text"] = "Si"
        drafts["manual"]["a"] = 5.43
        drafts["manual"]["atom_rows"] = saved_rows
        ui._store_structure_drafts(drafts)

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
            mock.patch.object(ui.st, "text_input", return_value="Si"),
            mock.patch.object(ui.st, "columns", side_effect=fake_columns),
            mock.patch.object(ui.st, "radio", return_value=ui.MANUAL_COORDINATE_MODE_FRACTIONAL),
            mock.patch.object(ui.st, "data_editor", side_effect=fake_data_editor),
            mock.patch.object(ui.st, "rerun"),
        ):
            ui._render_manual_builder_mode()

        expected_key = ui._draft_widget_key(ui.MANUAL_ATOM_TABLE_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY)
        self.assertEqual(observed["key"], expected_key)
        self.assertEqual(observed["data"], saved_rows)
        self.assertIsNone(observed["session_value"])
        self.assertNotIn(expected_key, st.session_state)
        self.assertEqual(ui._get_structure_drafts()["manual"]["atom_rows"], saved_rows)

    def test_durable_drafts_survive_widget_key_cleanup(self) -> None:
        drafts = ui._default_structure_drafts()
        drafts["selected_mode"] = ui.SOURCE_MODE_MANUAL
        drafts["file"] = {
            "requested_format": ui.FILE_FORMAT_CIF,
            "filename": "saved.cif",
            "file_bytes": b"cif-data",
        }
        drafts["manual"].update(
            {
                "formula_text": "Si2",
                "a": 5.43,
                "coordinate_mode": ui.MANUAL_COORDINATE_MODE_CARTESIAN,
                "atom_rows": [{"symbol": "Si", "x": 1.0, "y": 2.0, "z": 3.0}],
            }
        )
        ui._store_structure_drafts(drafts)
        st.session_state[ui.DRAFT_MODE_WIDGET_KEY] = ui.SOURCE_MODE_MANUAL
        st.session_state[ui._draft_widget_key(ui.MANUAL_A_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY)] = 5.43

        st.session_state.pop(ui.DRAFT_MODE_WIDGET_KEY)
        st.session_state.pop(ui._draft_widget_key(ui.MANUAL_A_WIDGET_KEY, ui.MANUAL_DRAFT_NONCE_STATE_KEY))

        restored = ui._get_structure_drafts()
        self.assertEqual(restored["selected_mode"], ui.SOURCE_MODE_MANUAL)
        self.assertEqual(restored["file"]["filename"], "saved.cif")
        self.assertEqual(restored["file"]["file_bytes"], b"cif-data")
        self.assertEqual(restored["manual"]["formula_text"], "Si2")
        self.assertEqual(restored["manual"]["a"], 5.43)
        self.assertEqual(restored["manual"]["coordinate_mode"], ui.MANUAL_COORDINATE_MODE_CARTESIAN)

    def test_file_draft_retains_uploaded_bytes_when_uploader_widget_disappears(self) -> None:
        uploaded_file = SimpleNamespace(name="sample.cif", getvalue=lambda: b"sample-bytes")

        def fake_columns(spec, **_kwargs):
            count = spec if isinstance(spec, int) else len(spec)
            return [_FakeColumn() for _ in range(count)]

        with (
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "selectbox", return_value=ui.FILE_FORMAT_CIF),
            mock.patch.object(ui.st, "file_uploader", side_effect=[uploaded_file, None]),
            mock.patch.object(ui.st, "columns", side_effect=fake_columns),
        ):
            ui._render_file_import_mode()
            uploader_key = ui._draft_widget_key(ui.FILE_UPLOADER_KEY, ui.FILE_DRAFT_NONCE_STATE_KEY)
            st.session_state.pop(uploader_key, None)
            ui._render_file_import_mode()

        restored = ui._get_structure_drafts()["file"]
        self.assertEqual(restored["requested_format"], ui.FILE_FORMAT_CIF)
        self.assertEqual(restored["filename"], "sample.cif")
        self.assertEqual(restored["file_bytes"], b"sample-bytes")

    def test_file_import_can_reuse_retained_payload(self) -> None:
        drafts = ui._default_structure_drafts()
        drafts["file"] = {
            "requested_format": ui.FILE_FORMAT_CIF,
            "filename": "saved.cif",
            "file_bytes": b"cif-data",
        }
        ui._store_structure_drafts(drafts)
        result = SimpleNamespace(warnings=())

        with (
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "selectbox", return_value=ui.FILE_FORMAT_CIF),
            mock.patch.object(ui.st, "file_uploader", return_value=None),
            mock.patch.object(
                ui.st,
                "columns",
                return_value=[_FakeColumn(button_clicked=True), _FakeColumn()],
            ),
            mock.patch.object(ui, "import_structure_file", return_value=result) as import_mock,
            mock.patch.object(ui, "_commit_structure_result") as commit_mock,
            mock.patch.object(ui.st, "rerun"),
        ):
            ui._render_file_import_mode()

        import_mock.assert_called_once_with(
            filename="saved.cif",
            file_bytes=b"cif-data",
            requested_format=ui.FILE_FORMAT_CIF,
        )
        commit_mock.assert_called_once_with(result, payload_bytes=b"cif-data")

    def test_invalid_durable_draft_values_fall_back_to_supported_defaults(self) -> None:
        normalized = ui._normalize_structure_drafts(
            {
                "selected_mode": "unsupported",
                "file": {
                    "requested_format": "bad-format",
                    "filename": "orphan.cif",
                    "file_bytes": None,
                },
                "manual": {
                    "a": float("nan"),
                    "alpha": 999.0,
                    "coordinate_mode": "bad-mode",
                },
                "prototype": {
                    "selected_family": "removed-family",
                    "families": {},
                },
            }
        )

        self.assertEqual(normalized["selected_mode"], ui.SOURCE_MODE_FILE)
        self.assertEqual(normalized["file"], ui._default_file_draft())
        self.assertEqual(normalized["manual"]["a"], 6.0)
        self.assertEqual(normalized["manual"]["alpha"], 180.0)
        self.assertEqual(
            normalized["manual"]["coordinate_mode"],
            ui.MANUAL_COORDINATE_MODE_FRACTIONAL,
        )
        self.assertEqual(
            normalized["prototype"]["selected_family"],
            ui.DEFAULT_PROTOTYPE_FAMILY_KEY,
        )

    def test_resetting_one_draft_preserves_other_drafts(self) -> None:
        drafts = ui._default_structure_drafts()
        drafts["file"] = {
            "requested_format": ui.FILE_FORMAT_CIF,
            "filename": "saved.cif",
            "file_bytes": b"cif-data",
        }
        drafts["manual"]["a"] = 8.0
        ui._store_structure_drafts(drafts)

        ui._reset_manual_builder_draft()

        restored = ui._get_structure_drafts()
        self.assertEqual(restored["manual"], ui._default_manual_draft())
        self.assertEqual(restored["file"]["filename"], "saved.cif")
        self.assertEqual(restored["file"]["file_bytes"], b"cif-data")

    def test_reset_file_draft_removes_retained_payload(self) -> None:
        drafts = ui._default_structure_drafts()
        drafts["file"] = {
            "requested_format": ui.FILE_FORMAT_CIF,
            "filename": "saved.cif",
            "file_bytes": b"cif-data",
        }
        ui._store_structure_drafts(drafts)

        ui._reset_file_import_draft()

        self.assertEqual(ui._get_structure_drafts()["file"], ui._default_file_draft())

    def test_clear_active_structure_does_not_remove_drafts(self) -> None:
        drafts = ui._default_structure_drafts()
        drafts["selected_mode"] = ui.SOURCE_MODE_PROTOTYPE
        ui._store_structure_drafts(drafts)

        structure_state.clear_structure_state()

        self.assertEqual(
            st.session_state[ui.STRUCTURE_DRAFTS_STATE_KEY]["selected_mode"],
            ui.SOURCE_MODE_PROTOTYPE,
        )

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
        ui._update_prototype_draft_values(first_family, {"lattice_parameter": 10.5})
        ui._update_prototype_draft_values(second_family, {"lattice_parameter": 7.25})

        ui._reset_prototype_builder_draft(first_family)

        self.assertEqual(st.session_state[ui._prototype_family_nonce_state_key(first_family)], 1)
        self.assertNotIn(ui._prototype_family_nonce_state_key(second_family), st.session_state)
        prototype_draft = ui._get_structure_drafts()["prototype"]
        self.assertEqual(prototype_draft["families"][first_family]["lattice_parameter"], 9.5)
        self.assertEqual(prototype_draft["families"][second_family]["lattice_parameter"], 7.25)

    def test_render_structure_mode_controls_defaults_atomic_positions_toggle_to_fractional(self) -> None:
        atoms = SimpleNamespace()
        ctx = SimpleNamespace(
            raw_atoms=atoms,
            structure=SimpleNamespace(mode_status=None),
        )
        observed: dict[str, object] = {}

        def fake_selectbox(label, options, *, index=0, **_kwargs):
            observed.setdefault("selectbox_labels", []).append(label)
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
        self.assertEqual(
            observed["selectbox_labels"],
            ["Active cell mode", "Active supercell"],
        )

    def test_render_structure_mode_controls_applies_selected_supercell(self) -> None:
        atoms = SimpleNamespace()
        ctx = SimpleNamespace(
            raw_atoms=atoms,
            structure=SimpleNamespace(mode_status=None),
        )

        def fake_selectbox(label, options, *, index=0, **_kwargs):
            if label == "Active supercell":
                return "preset_222"
            return options[index]

        with (
            mock.patch.object(ui.st, "markdown"),
            mock.patch.object(ui.st, "info"),
            mock.patch.object(ui.st, "warning"),
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "selectbox", side_effect=fake_selectbox),
            mock.patch.object(
                ui.st,
                "radio",
                return_value=ui.ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS[0],
            ),
            mock.patch.object(ui.st, "rerun"),
            mock.patch.object(ui, "get_structure_mode", return_value="source"),
            mock.patch.object(
                ui,
                "get_structure_supercell_repeat",
                return_value=(1, 1, 1),
            ),
            mock.patch.object(ui, "apply_structure_supercell_selection") as apply_mock,
        ):
            ui._render_structure_mode_controls(ctx)

        apply_mock.assert_called_once_with((2, 2, 2))

    def test_render_structure_mode_controls_restores_supercell_after_widget_cleanup(self) -> None:
        ctx = SimpleNamespace(
            raw_atoms=SimpleNamespace(),
            structure=SimpleNamespace(mode_status=None),
        )
        observed: dict[str, int] = {}

        def fake_selectbox(label, options, *, index=0, **_kwargs):
            if label == "Active supercell":
                observed["supercell_index"] = index
            return options[index]

        with (
            mock.patch.object(ui.st, "markdown"),
            mock.patch.object(ui.st, "info"),
            mock.patch.object(ui.st, "warning"),
            mock.patch.object(ui.st, "caption"),
            mock.patch.object(ui.st, "selectbox", side_effect=fake_selectbox),
            mock.patch.object(
                ui.st,
                "radio",
                return_value=ui.ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS[0],
            ),
            mock.patch.object(ui, "get_structure_mode", return_value="source"),
            mock.patch.object(
                ui,
                "get_structure_supercell_repeat",
                return_value=(2, 2, 2),
            ),
            mock.patch.object(ui, "apply_structure_supercell_selection") as apply_mock,
        ):
            ui._render_structure_mode_controls(ctx)

        self.assertEqual(observed["supercell_index"], 2)
        apply_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
