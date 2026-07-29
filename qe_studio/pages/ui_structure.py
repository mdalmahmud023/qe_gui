from __future__ import annotations

import math
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from ..core.project_context import ProjectContext
from ..core.state_keys import (
    ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS,
    ERROR_STATE_KEY,
    STRUCTURE_DRAFTS_STATE_KEY,
    STRUCTURE_MODE_OPTIONS,
)
from ..core.structure_state import (
    apply_structure_mode_selection,
    apply_structure_supercell_selection,
    clear_structure_state,
    commit_structure_source,
    get_atomic_positions_coordinate_mode,
    get_structure_mode,
    get_structure_supercell_repeat,
    set_atomic_positions_coordinate_mode,
    set_structure_error,
)
from ..services.prototype_catalog import (
    DEFAULT_PROTOTYPE_FAMILY_KEY,
    FIELD_KIND_FLOAT,
    PrototypeFamilyDefinition,
    PrototypeFieldDefinition,
    build_prototype_family,
    get_prototype_family,
    get_prototype_family_keys,
)
from ..services.structure_sources import (
    FILE_FORMAT_AUTO,
    FILE_FORMAT_CIF,
    FILE_FORMAT_VASP,
    FILE_FORMAT_XSF,
    FILE_FORMAT_XYZ,
    MANUAL_COORDINATE_MODE_CARTESIAN,
    MANUAL_COORDINATE_MODE_FRACTIONAL,
    build_manual_structure,
    import_structure_file,
)
from ..services.structure_viewer import (
    LABEL_MODE_ELEMENT,
    LABEL_MODE_ELEMENT_INDEX,
    LABEL_MODE_OFF,
    LABEL_MODE_OPTIONS,
    STYLE_MODE_BALL_AND_STICK,
    STYLE_MODE_OPTIONS,
    STYLE_MODE_SPACEFILL,
    STYLE_MODE_STICK,
    SUPERCELL_MODE_CUSTOM,
    SUPERCELL_MODE_OPTIONS,
    VIEWER_LABEL_MODE_WIDGET_KEY,
    VIEWER_SHOW_AXES_WIDGET_KEY,
    VIEWER_SHOW_BONDS_WIDGET_KEY,
    VIEWER_SHOW_UNIT_CELL_WIDGET_KEY,
    VIEWER_STYLE_MODE_WIDGET_KEY,
    VIEWER_SUPERCELL_A_WIDGET_KEY,
    VIEWER_SUPERCELL_B_WIDGET_KEY,
    VIEWER_SUPERCELL_C_WIDGET_KEY,
    VIEWER_SUPERCELL_MODE_WIDGET_KEY,
    VIEWER_VISIBLE_SPECIES_WIDGET_KEY,
    ViewerSettings,
    build_display_atoms,
    build_structure_viewer_html,
    format_supercell_repeat,
    get_effective_show_bonds,
    get_sorted_species,
    get_supercell_repeat_for_mode,
    normalize_visible_species,
    style_supports_bond_toggle,
)
from .shared import render_status_messages


SOURCE_MODE_FILE = "file"
SOURCE_MODE_MANUAL = "manual"
SOURCE_MODE_PROTOTYPE = "prototype"
SOURCE_MODE_OPTIONS = (SOURCE_MODE_FILE, SOURCE_MODE_MANUAL, SOURCE_MODE_PROTOTYPE)

DRAFT_MODE_WIDGET_KEY = "structure_draft_mode_widget"
ACTIVE_NOTICE_STATE_KEY = "structure_active_notice_messages"
FILE_FORMAT_WIDGET_KEY = "structure_file_format_widget"
FILE_UPLOADER_KEY = "structure_file_uploader"
FILE_DRAFT_NONCE_STATE_KEY = "structure_file_draft_nonce"
MANUAL_FORMULA_WIDGET_KEY = "structure_manual_formula"
MANUAL_A_WIDGET_KEY = "structure_manual_a"
MANUAL_B_WIDGET_KEY = "structure_manual_b"
MANUAL_C_WIDGET_KEY = "structure_manual_c"
MANUAL_ALPHA_WIDGET_KEY = "structure_manual_alpha"
MANUAL_BETA_WIDGET_KEY = "structure_manual_beta"
MANUAL_GAMMA_WIDGET_KEY = "structure_manual_gamma"
MANUAL_COORDINATE_MODE_WIDGET_KEY = "structure_manual_coordinate_mode"
MANUAL_ATOM_TABLE_WIDGET_KEY = "structure_manual_atom_table"
MANUAL_DRAFT_NONCE_STATE_KEY = "structure_manual_draft_nonce"
PROTOTYPE_FAMILY_WIDGET_KEY = "structure_prototype_family"
PROTOTYPE_FIELD_WIDGET_KEY_PREFIX = "structure_prototype_field"
PROTOTYPE_DRAFT_NONCE_STATE_KEY = "structure_prototype_draft_nonce"

ACTIVE_BANNER_CONTAINER_KEY = "structure_active_banner"
DRAFT_PANEL_CONTAINER_KEY = "structure_draft_panel"
INSPECT_PANEL_CONTAINER_KEY = "structure_inspect_panel"


def _default_file_draft() -> dict[str, Any]:
    return {
        "requested_format": FILE_FORMAT_AUTO,
        "filename": None,
        "file_bytes": None,
    }


def _default_manual_draft() -> dict[str, Any]:
    return {
        "formula_text": "",
        "a": 6.0,
        "b": 6.0,
        "c": 6.0,
        "alpha": 90.0,
        "beta": 90.0,
        "gamma": 90.0,
        "coordinate_mode": MANUAL_COORDINATE_MODE_FRACTIONAL,
        "atom_rows": _default_manual_atom_rows(),
    }


def _default_prototype_draft() -> dict[str, Any]:
    return {
        "selected_family": DEFAULT_PROTOTYPE_FAMILY_KEY,
        "families": {},
    }


def _default_structure_drafts() -> dict[str, Any]:
    return {
        "selected_mode": SOURCE_MODE_FILE,
        "file": _default_file_draft(),
        "manual": _default_manual_draft(),
        "prototype": _default_prototype_draft(),
    }


def _coerce_bounded_float(
    value: object,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        candidate = float(default)
    if not math.isfinite(candidate):
        candidate = float(default)
    if minimum is not None:
        candidate = max(candidate, float(minimum))
    if maximum is not None:
        candidate = min(candidate, float(maximum))
    return candidate


def _normalize_manual_atom_rows(rows: object) -> list[dict[str, object]]:
    if hasattr(rows, "to_dict"):
        try:
            records = rows.to_dict("records")
        except (TypeError, ValueError):
            records = None
    elif isinstance(rows, list):
        records = rows
    else:
        records = None

    if records is None:
        return _default_manual_atom_rows()

    normalized_rows: list[dict[str, object]] = []
    for row in records:
        row_data = row if isinstance(row, dict) else {}
        normalized_rows.append(
            {
                "symbol": row_data.get("symbol", ""),
                "x": row_data.get("x", 0.0),
                "y": row_data.get("y", 0.0),
                "z": row_data.get("z", 0.0),
            }
        )
    return normalized_rows


def _normalize_file_draft(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    requested_format = raw.get("requested_format", FILE_FORMAT_AUTO)
    supported_formats = (FILE_FORMAT_AUTO, FILE_FORMAT_CIF, FILE_FORMAT_VASP, FILE_FORMAT_XSF, FILE_FORMAT_XYZ)
    if requested_format not in supported_formats:
        requested_format = FILE_FORMAT_AUTO

    filename = raw.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        filename = None

    file_bytes = raw.get("file_bytes")
    if isinstance(file_bytes, bytearray):
        file_bytes = bytes(file_bytes)
    if not isinstance(file_bytes, bytes):
        file_bytes = None
    if filename is None or file_bytes is None:
        filename = None
        file_bytes = None

    return {
        "requested_format": requested_format,
        "filename": filename,
        "file_bytes": file_bytes,
    }


def _normalize_manual_draft(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    coordinate_mode = raw.get("coordinate_mode", MANUAL_COORDINATE_MODE_FRACTIONAL)
    if coordinate_mode not in (MANUAL_COORDINATE_MODE_FRACTIONAL, MANUAL_COORDINATE_MODE_CARTESIAN):
        coordinate_mode = MANUAL_COORDINATE_MODE_FRACTIONAL
    return {
        "formula_text": str(raw.get("formula_text") or ""),
        "a": _coerce_bounded_float(raw.get("a"), 6.0, minimum=0.0),
        "b": _coerce_bounded_float(raw.get("b"), 6.0, minimum=0.0),
        "c": _coerce_bounded_float(raw.get("c"), 6.0, minimum=0.0),
        "alpha": _coerce_bounded_float(raw.get("alpha"), 90.0, minimum=0.0, maximum=180.0),
        "beta": _coerce_bounded_float(raw.get("beta"), 90.0, minimum=0.0, maximum=180.0),
        "gamma": _coerce_bounded_float(raw.get("gamma"), 90.0, minimum=0.0, maximum=180.0),
        "coordinate_mode": coordinate_mode,
        "atom_rows": _normalize_manual_atom_rows(raw.get("atom_rows")),
    }


def _normalize_prototype_family_values(
    family: PrototypeFamilyDefinition,
    value: object,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    normalized: dict[str, Any] = {}
    for field in family.all_fields:
        field_value = raw.get(field.key, field.default)
        if field.input_kind == FIELD_KIND_FLOAT:
            normalized[field.key] = _coerce_bounded_float(
                field_value,
                float(field.default),
                minimum=field.min_value,
                maximum=field.max_value,
            )
        else:
            normalized[field.key] = str(field_value)
    return normalized


def _normalize_prototype_draft(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    family_keys = get_prototype_family_keys()
    selected_family = raw.get("selected_family", DEFAULT_PROTOTYPE_FAMILY_KEY)
    if selected_family not in family_keys:
        selected_family = DEFAULT_PROTOTYPE_FAMILY_KEY

    raw_families = raw.get("families")
    if not isinstance(raw_families, dict):
        raw_families = {}
    families = {
        family_key: _normalize_prototype_family_values(
            get_prototype_family(family_key),
            raw_families.get(family_key),
        )
        for family_key in family_keys
        if family_key in raw_families
    }
    return {
        "selected_family": selected_family,
        "families": families,
    }


def _normalize_structure_drafts(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    selected_mode = raw.get("selected_mode", SOURCE_MODE_FILE)
    if selected_mode not in SOURCE_MODE_OPTIONS:
        selected_mode = SOURCE_MODE_FILE
    return {
        "selected_mode": selected_mode,
        "file": _normalize_file_draft(raw.get("file")),
        "manual": _normalize_manual_draft(raw.get("manual")),
        "prototype": _normalize_prototype_draft(raw.get("prototype")),
    }


def _get_structure_drafts() -> dict[str, Any]:
    normalized = _normalize_structure_drafts(st.session_state.get(STRUCTURE_DRAFTS_STATE_KEY))
    st.session_state[STRUCTURE_DRAFTS_STATE_KEY] = normalized
    return normalized


def _store_structure_drafts(drafts: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_structure_drafts(drafts)
    st.session_state[STRUCTURE_DRAFTS_STATE_KEY] = normalized
    return normalized


def _update_prototype_draft_values(
    family_key: str,
    values: dict[str, Any],
    *,
    select_family: bool = False,
) -> None:
    family = get_prototype_family(family_key)
    drafts = _get_structure_drafts()
    prototype_draft = dict(drafts["prototype"])
    families = dict(prototype_draft["families"])
    current_values = dict(families.get(family_key) or {})
    current_values.update(values)
    families[family_key] = _normalize_prototype_family_values(family, current_values)
    prototype_draft["families"] = families
    if select_family:
        prototype_draft["selected_family"] = family_key
    drafts["prototype"] = prototype_draft
    _store_structure_drafts(drafts)


def render_page(ctx: ProjectContext) -> None:
    """Render the Structure page."""
    _render_structure_page_styles()
    st.subheader("Structure")
    st.caption(
        "Prepare a draft structure source, apply it as the active structure, and inspect the active cell in 3D before moving to Setup."
    )

    _render_active_structure_banner(ctx)

    with st.container(key=DRAFT_PANEL_CONTAINER_KEY, border=True):
        draft_left_col, draft_center_col, draft_right_col = st.columns([0.06, 0.88, 0.06], gap="small")
        with draft_center_col:
            _render_structure_draft_panel()

    st.markdown("")
    with st.container(key=INSPECT_PANEL_CONTAINER_KEY, border=True):
        _render_active_structure_inspector(ctx)


def _render_structure_page_styles() -> None:
    st.markdown(
        """
        <style>
        .st-key-structure_active_banner {
            position: sticky;
            top: 4.1rem;
            z-index: 20;
            margin-bottom: 0.9rem;
        }
        .st-key-structure_active_banner [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 0.95rem;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.10);
        }
        .st-key-structure_draft_panel [data-testid="stVerticalBlockBorderWrapper"],
        .st-key-structure_inspect_panel [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 0.95rem;
        }
        .qe-structure-chip {
            display: inline-block;
            border-radius: 999px;
            padding: 0.15rem 0.6rem;
            border: 1px solid rgba(99, 102, 241, 0.28);
            background: rgba(99, 102, 241, 0.10);
            font-size: 0.8rem;
            font-weight: 600;
            margin-right: 0.4rem;
        }
        .qe-structure-note {
            font-size: 0.92rem;
            opacity: 0.88;
        }
        @media (max-width: 900px) {
            .st-key-structure_active_banner {
                position: static;
                top: auto;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_active_structure_banner(ctx: ProjectContext) -> None:
    structure = ctx.structure
    with st.container(key=ACTIVE_BANNER_CONTAINER_KEY, border=True):
        title_col, action_col = st.columns([0.86, 0.14], vertical_alignment="top")
        with title_col:
            st.markdown("#### Active Structure")
            st.markdown(
                "<div class='qe-structure-note'>Setup, Run, and Analysis use only the active structure shown here. Draft forms stay separate until you apply them.</div>",
                unsafe_allow_html=True,
            )
        with action_col:
            clear_clicked = st.button(
                "Clear Active",
                key="structure_clear_active_button",
                use_container_width=True,
                disabled=not structure.loaded,
            )
        if clear_clicked:
            clear_structure_state()
            st.session_state.pop(ACTIVE_NOTICE_STATE_KEY, None)
            st.rerun()

        if not structure.loaded:
            st.info("No active structure is set yet. Import or build a draft, then apply it to continue.")
            return

        meta_cols = st.columns([1.35, 0.65, 0.95, 1.45, 1.0, 1.25])
        _render_compact_metadata(meta_cols[0], "Formula", structure.formula or "Unknown")
        _render_compact_metadata(meta_cols[1], "Atoms", str(structure.atom_count))
        _render_compact_metadata(meta_cols[2], "Source Type", structure.source_kind_label or "Unknown")
        _render_compact_metadata(meta_cols[3], "Source Label", structure.source_label or "Unknown")
        _render_compact_metadata(meta_cols[4], "Cell Mode", structure.cell_mode_label)
        with meta_cols[5]:
            st.caption("Status")
            status_chip = "Primitive derived" if structure.cell_mode == "primitive" else "Using source cell"
            st.markdown(f"<span class='qe-structure-chip'>{status_chip}</span>", unsafe_allow_html=True)

        if structure.mode_status:
            if "failed" in structure.mode_status.lower():
                st.warning(structure.mode_status)
            elif structure.mode_status not in {"Using the source cell.", "The source structure already looks like a primitive cell."}:
                st.caption(structure.mode_status)

        render_status_messages(_collect_active_structure_messages(ctx))


def _render_compact_metadata(column, label: str, value: str) -> None:
    with column:
        st.caption(label)
        st.markdown(f"**{value}**")


def _collect_active_structure_messages(ctx: ProjectContext) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for notice in st.session_state.get(ACTIVE_NOTICE_STATE_KEY, []) or []:
        if notice:
            messages.append(("caption", str(notice)))

    if ctx.pseudopotentials.state == "stale" and ctx.pseudopotentials.message:
        messages.append(("warning", ctx.pseudopotentials.message))
    if ctx.input_files.provenance_state == "out_of_sync" and ctx.input_files.provenance_message:
        messages.append(("warning", ctx.input_files.provenance_message))
    if ctx.artifacts.provenance_state == "out_of_sync" and ctx.artifacts.provenance_message:
        messages.append(("warning", ctx.artifacts.provenance_message))

    deduplicated: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        if message not in seen:
            deduplicated.append(message)
            seen.add(message)
    return deduplicated


@st.fragment
def _render_structure_draft_panel() -> None:
    st.markdown("#### Import / Build Draft")
    st.caption("Changing draft modes preserves the current active structure. A draft becomes active only after explicit apply.")

    drafts = _get_structure_drafts()
    selected_mode_value = drafts["selected_mode"]
    selected_mode = st.radio(
        "Draft Source",
        options=SOURCE_MODE_OPTIONS,
        index=SOURCE_MODE_OPTIONS.index(selected_mode_value),
        horizontal=True,
        key=DRAFT_MODE_WIDGET_KEY,
        format_func=lambda value: {
            SOURCE_MODE_FILE: "Import File",
            SOURCE_MODE_MANUAL: "Manual Builder",
            SOURCE_MODE_PROTOTYPE: "Prototype Builder",
        }[value],
    )
    if selected_mode != selected_mode_value:
        drafts["selected_mode"] = selected_mode
        _store_structure_drafts(drafts)

    error_message = st.session_state.get(ERROR_STATE_KEY)
    if error_message:
        st.error(error_message)

    if selected_mode == SOURCE_MODE_FILE:
        _render_file_import_mode()
    elif selected_mode == SOURCE_MODE_MANUAL:
        _render_manual_builder_mode()
    else:
        _render_prototype_builder_mode()


def _render_file_import_mode() -> None:
    st.caption("Draft only until applied. Supported v1 formats: CIF, VASP POSCAR/CONTCAR, XSF, and periodic XYZ / extXYZ.")
    drafts = _get_structure_drafts()
    file_draft = drafts["file"]
    format_options = (FILE_FORMAT_AUTO, FILE_FORMAT_CIF, FILE_FORMAT_VASP, FILE_FORMAT_XSF, FILE_FORMAT_XYZ)
    selected_format_key = _draft_widget_key(FILE_FORMAT_WIDGET_KEY, FILE_DRAFT_NONCE_STATE_KEY)
    uploaded_file_key = _draft_widget_key(FILE_UPLOADER_KEY, FILE_DRAFT_NONCE_STATE_KEY)
    selected_format = st.selectbox(
        "File format",
        options=format_options,
        index=format_options.index(file_draft["requested_format"]),
        key=selected_format_key,
        format_func=lambda value: {
            FILE_FORMAT_AUTO: "Auto-detect from filename",
            FILE_FORMAT_CIF: "CIF",
            FILE_FORMAT_VASP: "VASP POSCAR/CONTCAR",
            FILE_FORMAT_XSF: "XSF",
            FILE_FORMAT_XYZ: "XYZ / extXYZ",
        }[value],
    )
    uploaded_file = st.file_uploader("Structure file", key=uploaded_file_key)

    retained_filename = file_draft["filename"]
    retained_file_bytes = file_draft["file_bytes"]
    if uploaded_file is not None:
        retained_filename = uploaded_file.name
        retained_file_bytes = uploaded_file.getvalue()

    drafts["file"] = {
        "requested_format": selected_format,
        "filename": retained_filename,
        "file_bytes": retained_file_bytes,
    }
    file_draft = _store_structure_drafts(drafts)["file"]

    if file_draft["filename"] is not None and file_draft["file_bytes"] is not None:
        st.caption(
            f"Retained draft file: `{file_draft['filename']}` "
            f"({len(file_draft['file_bytes']):,} bytes). Reset Draft removes it."
        )

    action_col, reset_col = st.columns(2)
    apply_clicked = action_col.button(
        "Import and Set Active",
        key="structure_file_import_apply_button",
        use_container_width=True,
    )
    reset_clicked = reset_col.button(
        "Reset Draft",
        key="structure_file_import_reset_button",
        use_container_width=True,
    )

    if reset_clicked:
        _reset_file_import_draft()
        st.rerun()
        return
    if not apply_clicked:
        return
    if file_draft["filename"] is None or file_draft["file_bytes"] is None:
        set_structure_error("Select a structure file to import.")
        st.rerun()
        return

    try:
        file_bytes = file_draft["file_bytes"]
        result = import_structure_file(
            filename=file_draft["filename"],
            file_bytes=file_bytes,
            requested_format=selected_format,
        )
        _commit_structure_result(result, payload_bytes=file_bytes)
    except Exception as exc:
        set_structure_error(f"Failed to import the structure file: {exc}")
    st.rerun()


def _render_manual_builder_mode() -> None:
    st.caption("Draft only until applied. Enter a periodic crystal source cell using lattice parameters plus an atom table.")
    drafts = _get_structure_drafts()
    manual_draft = drafts["manual"]
    formula_key = _draft_widget_key(MANUAL_FORMULA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    a_key = _draft_widget_key(MANUAL_A_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    b_key = _draft_widget_key(MANUAL_B_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    c_key = _draft_widget_key(MANUAL_C_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    alpha_key = _draft_widget_key(MANUAL_ALPHA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    beta_key = _draft_widget_key(MANUAL_BETA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    gamma_key = _draft_widget_key(MANUAL_GAMMA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    coordinate_mode_key = _draft_widget_key(MANUAL_COORDINATE_MODE_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    atom_table_key = _draft_widget_key(MANUAL_ATOM_TABLE_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    formula_text = st.text_input(
        "Formula (optional validation only)",
        value=manual_draft["formula_text"],
        key=formula_key,
        help="Use this only to validate the atom table. The structure is built from the rows below, not from the formula text.",
    )

    row_one_col1, row_one_col2, row_one_col3 = st.columns(3)
    a_value = row_one_col1.number_input("a (A)", min_value=0.0, value=manual_draft["a"], step=0.1, key=a_key)
    b_value = row_one_col2.number_input("b (A)", min_value=0.0, value=manual_draft["b"], step=0.1, key=b_key)
    c_value = row_one_col3.number_input("c (A)", min_value=0.0, value=manual_draft["c"], step=0.1, key=c_key)

    row_two_col1, row_two_col2, row_two_col3 = st.columns(3)
    alpha_value = row_two_col1.number_input(
        "alpha (deg)",
        min_value=0.0,
        max_value=180.0,
        value=manual_draft["alpha"],
        step=1.0,
        key=alpha_key,
    )
    beta_value = row_two_col2.number_input(
        "beta (deg)",
        min_value=0.0,
        max_value=180.0,
        value=manual_draft["beta"],
        step=1.0,
        key=beta_key,
    )
    gamma_value = row_two_col3.number_input(
        "gamma (deg)",
        min_value=0.0,
        max_value=180.0,
        value=manual_draft["gamma"],
        step=1.0,
        key=gamma_key,
    )

    coordinate_options = (MANUAL_COORDINATE_MODE_FRACTIONAL, MANUAL_COORDINATE_MODE_CARTESIAN)
    coordinate_mode = st.radio(
        "Coordinate mode",
        options=coordinate_options,
        index=coordinate_options.index(manual_draft["coordinate_mode"]),
        horizontal=True,
        key=coordinate_mode_key,
        format_func=lambda value: "Fractional" if value == MANUAL_COORDINATE_MODE_FRACTIONAL else "Cartesian",
    )
    atom_rows = st.data_editor(
        manual_draft["atom_rows"],
        key=atom_table_key,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )

    drafts["manual"] = {
        "formula_text": formula_text,
        "a": a_value,
        "b": b_value,
        "c": c_value,
        "alpha": alpha_value,
        "beta": beta_value,
        "gamma": gamma_value,
        "coordinate_mode": coordinate_mode,
        "atom_rows": _normalize_manual_atom_rows(atom_rows),
    }
    manual_draft = _store_structure_drafts(drafts)["manual"]

    action_col, reset_col = st.columns(2)
    apply_clicked = action_col.button(
        "Build and Set Active",
        key="structure_manual_build_apply_button",
        use_container_width=True,
    )
    reset_clicked = reset_col.button(
        "Reset Draft",
        key="structure_manual_build_reset_button",
        use_container_width=True,
    )

    if reset_clicked:
        _reset_manual_builder_draft()
        st.rerun()
        return
    if not apply_clicked:
        return

    try:
        result = build_manual_structure(
            a=manual_draft["a"],
            b=manual_draft["b"],
            c=manual_draft["c"],
            alpha=manual_draft["alpha"],
            beta=manual_draft["beta"],
            gamma=manual_draft["gamma"],
            coordinate_mode=manual_draft["coordinate_mode"],
            atom_rows=manual_draft["atom_rows"],
            formula_text=manual_draft["formula_text"],
        )
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the manual structure: {exc}")
    st.rerun()


def _render_prototype_builder_mode() -> None:
    drafts = _get_structure_drafts()
    prototype_draft = drafts["prototype"]
    family_keys = get_prototype_family_keys()
    selected_family_key = st.selectbox(
        "Prototype Family",
        options=family_keys,
        index=family_keys.index(prototype_draft["selected_family"]),
        key=PROTOTYPE_FAMILY_WIDGET_KEY,
        format_func=lambda family_key: get_prototype_family(family_key).source_label,
    )
    family = get_prototype_family(selected_family_key)
    family_values = _normalize_prototype_family_values(
        family,
        prototype_draft["families"].get(selected_family_key),
    )
    st.caption(f"Draft only until applied. {family.short_description}")
    st.caption(f"Formula template: `{family.formula_template}`")

    family_values = _render_prototype_family_fields(family, family_values)
    _update_prototype_draft_values(selected_family_key, family_values, select_family=True)

    action_col, reset_col = st.columns(2)
    apply_clicked = action_col.button(
        "Build and Set Active",
        key="structure_prototype_build_apply_button",
        use_container_width=True,
    )
    reset_clicked = reset_col.button(
        "Reset Draft",
        key="structure_prototype_build_reset_button",
        use_container_width=True,
    )

    if reset_clicked:
        _reset_prototype_builder_draft(selected_family_key)
        st.rerun()
        return
    if not apply_clicked:
        return

    try:
        result = build_prototype_family(selected_family_key, family_values)
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the prototype structure: {exc}")
    st.rerun()


def _render_prototype_family_fields(
    family: PrototypeFamilyDefinition,
    values: dict[str, Any],
) -> dict[str, Any]:
    rendered_values: dict[str, Any] = {}
    text_fields = [field for field in family.core_fields if field.input_kind != FIELD_KIND_FLOAT]
    numeric_fields = [field for field in family.core_fields if field.input_kind == FIELD_KIND_FLOAT]

    if text_fields:
        for start in range(0, len(text_fields), 2):
            field_pair = text_fields[start : start + 2]
            columns = st.columns(2)
            for column, field in zip(columns, field_pair):
                with column:
                    rendered_values[field.key] = _render_prototype_field_input(
                        family,
                        field,
                        values.get(field.key, field.default),
                    )

    for field in numeric_fields:
        rendered_values[field.key] = _render_prototype_field_input(
            family,
            field,
            values.get(field.key, field.default),
        )

    if family.advanced_fields:
        with st.expander("Advanced prototype options", expanded=False):
            for field in family.advanced_fields:
                rendered_values[field.key] = _render_prototype_field_input(
                    family,
                    field,
                    values.get(field.key, field.default),
                )
    return rendered_values


def _render_prototype_field_input(
    family: PrototypeFamilyDefinition,
    field: PrototypeFieldDefinition,
    value: Any,
) -> Any:
    widget_key = _prototype_family_field_widget_key(family.key, field.key)
    if field.input_kind == FIELD_KIND_FLOAT:
        return st.number_input(
            field.label,
            min_value=field.min_value,
            max_value=field.max_value,
            value=float(value),
            step=field.step or 0.1,
            key=widget_key,
            help=field.help_text,
        )

    return st.text_input(
        field.label,
        value=str(value),
        key=widget_key,
        help=field.help_text,
    )


def _collect_prototype_family_values(family: PrototypeFamilyDefinition) -> dict[str, Any]:
    """Collect currently rendered prototype widget values for compatibility."""
    values: dict[str, Any] = {}
    for field in family.all_fields:
        widget_key = _prototype_family_field_widget_key(family.key, field.key)
        values[field.key] = st.session_state.get(widget_key, field.default)
    return values


def _render_active_structure_inspector(ctx: ProjectContext) -> None:
    st.markdown("#### Inspect Active Structure")
    st.caption("Keep the visualization on one side and the active display settings on the other. Viewer controls remain display-only.")

    if ctx.atoms is None:
        st.info("Apply a structure draft to inspect the active structure here.")
        return

    visualization_col, settings_col = st.columns([1.6, 1.0], gap="large")
    with settings_col:
        viewer_settings = _render_inspection_settings_panel(ctx)
    with visualization_col:
        _render_structure_visualization_panel(ctx, viewer_settings)


def _render_inspection_settings_panel(ctx: ProjectContext) -> ViewerSettings:
    st.markdown("##### Active Settings")
    _render_structure_mode_controls(ctx)
    viewer_settings = _render_structure_viewer_controls(ctx)
    _render_structure_metadata(ctx)
    return viewer_settings


def _render_structure_visualization_panel(ctx: ProjectContext, viewer_settings: ViewerSettings) -> None:
    st.markdown("##### Visualization")
    _render_structure_viewer(ctx, viewer_settings)


def _render_structure_mode_controls(ctx: ProjectContext) -> None:
    st.markdown("##### Cell Representation")
    if ctx.raw_atoms is None:
        st.info("Load or build a structure to choose between the source cell and a primitive cell.")
        return

    current_mode = get_structure_mode()
    selected_mode = st.selectbox(
        "Active cell mode",
        options=STRUCTURE_MODE_OPTIONS,
        format_func=lambda value: "Primitive Cell" if value == "primitive" else "Source Cell",
        index=STRUCTURE_MODE_OPTIONS.index(current_mode) if current_mode in STRUCTURE_MODE_OPTIONS else 0,
    )
    if selected_mode != current_mode:
        try:
            apply_structure_mode_selection(selected_mode)
        except Exception as exc:
            st.error(f"Failed to apply the selected cell mode: {exc}")
        else:
            st.rerun()

    current_repeat = get_structure_supercell_repeat()
    default_supercell_mode = _get_supercell_mode_for_repeat(current_repeat)
    stored_supercell_mode = st.session_state.get(VIEWER_SUPERCELL_MODE_WIDGET_KEY)
    if stored_supercell_mode not in SUPERCELL_MODE_OPTIONS:
        st.session_state.pop(VIEWER_SUPERCELL_MODE_WIDGET_KEY, None)
    selected_supercell_mode = st.selectbox(
        "Active supercell",
        options=SUPERCELL_MODE_OPTIONS,
        index=SUPERCELL_MODE_OPTIONS.index(default_supercell_mode),
        key=VIEWER_SUPERCELL_MODE_WIDGET_KEY,
        format_func=lambda value: {
            "preset_111": "1x1x1",
            "preset_211": "2x1x1",
            "preset_222": "2x2x2",
            SUPERCELL_MODE_CUSTOM: "Custom",
        }[value],
        help="Changes the active structure used by Setup, Studies, Run, and Analysis.",
    )
    if selected_supercell_mode == SUPERCELL_MODE_CUSTOM:
        st.session_state.setdefault(VIEWER_SUPERCELL_A_WIDGET_KEY, current_repeat[0])
        st.session_state.setdefault(VIEWER_SUPERCELL_B_WIDGET_KEY, current_repeat[1])
        st.session_state.setdefault(VIEWER_SUPERCELL_C_WIDGET_KEY, current_repeat[2])
        repeat_cols = st.columns(3)
        repeat_cols[0].number_input(
            "Repeat a",
            min_value=1,
            max_value=3,
            step=1,
            key=VIEWER_SUPERCELL_A_WIDGET_KEY,
        )
        repeat_cols[1].number_input(
            "Repeat b",
            min_value=1,
            max_value=3,
            step=1,
            key=VIEWER_SUPERCELL_B_WIDGET_KEY,
        )
        repeat_cols[2].number_input(
            "Repeat c",
            min_value=1,
            max_value=3,
            step=1,
            key=VIEWER_SUPERCELL_C_WIDGET_KEY,
        )
        selected_repeat = get_supercell_repeat_for_mode(
            selected_supercell_mode,
            custom_repeat=(
                st.session_state.get(VIEWER_SUPERCELL_A_WIDGET_KEY, 1),
                st.session_state.get(VIEWER_SUPERCELL_B_WIDGET_KEY, 1),
                st.session_state.get(VIEWER_SUPERCELL_C_WIDGET_KEY, 1),
            ),
        )
    else:
        selected_repeat = get_supercell_repeat_for_mode(selected_supercell_mode)

    if selected_repeat != current_repeat:
        try:
            apply_structure_supercell_selection(selected_repeat)
        except Exception as exc:
            st.error(f"Failed to apply the selected supercell: {exc}")
        else:
            st.rerun()

    current_coordinate_mode = get_atomic_positions_coordinate_mode()
    selected_coordinate_mode = st.radio(
        "Atomic positions coordinate system",
        options=ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS,
        index=ATOMIC_POSITIONS_COORDINATE_MODE_OPTIONS.index(current_coordinate_mode),
        horizontal=True,
        format_func=_format_atomic_positions_coordinate_mode_label,
    )
    if selected_coordinate_mode != current_coordinate_mode:
        set_atomic_positions_coordinate_mode(selected_coordinate_mode)

    status_bits: list[str] = []
    if ctx.structure.mode_status:
        if "failed" in ctx.structure.mode_status.lower():
            st.warning(ctx.structure.mode_status)
        elif ctx.structure.mode_status not in {"Using the source cell.", "The source structure already looks like a primitive cell."}:
            status_bits.append(ctx.structure.mode_status)
    status_bits.append(
        "Changing the active cell representation, supercell, or atomic-position coordinate system affects newly generated QE inputs. "
        "Regenerate `pw.in` before DOS or bands."
    )
    st.caption(" ".join(status_bits))


def _get_supercell_mode_for_repeat(repeat: tuple[int, int, int]) -> str:
    """Return the preset selector mode matching an active repeat tuple."""
    for mode in SUPERCELL_MODE_OPTIONS:
        if mode != SUPERCELL_MODE_CUSTOM and get_supercell_repeat_for_mode(mode) == repeat:
            return mode
    return SUPERCELL_MODE_CUSTOM


def _render_structure_viewer_controls(ctx: ProjectContext) -> ViewerSettings:
    st.markdown("##### Visualization Settings")
    available_species = get_sorted_species(ctx.atoms)
    normalized_species = normalize_visible_species(ctx.atoms, st.session_state.get(VIEWER_VISIBLE_SPECIES_WIDGET_KEY))
    st.session_state[VIEWER_VISIBLE_SPECIES_WIDGET_KEY] = list(normalized_species)
    st.session_state.setdefault(VIEWER_LABEL_MODE_WIDGET_KEY, LABEL_MODE_OFF)
    st.session_state.setdefault(VIEWER_STYLE_MODE_WIDGET_KEY, STYLE_MODE_BALL_AND_STICK)
    st.session_state.setdefault(VIEWER_SHOW_BONDS_WIDGET_KEY, False)
    st.session_state.setdefault(VIEWER_SHOW_UNIT_CELL_WIDGET_KEY, True)
    st.session_state.setdefault(VIEWER_SHOW_AXES_WIDGET_KEY, False)

    top_control_cols = st.columns(2, gap="medium")
    label_mode = top_control_cols[0].selectbox(
        "Labels",
        options=LABEL_MODE_OPTIONS,
        key=VIEWER_LABEL_MODE_WIDGET_KEY,
        format_func=lambda value: {
            LABEL_MODE_OFF: "Off",
            LABEL_MODE_ELEMENT: "Element",
            LABEL_MODE_ELEMENT_INDEX: "Element + Index",
        }[value],
    )
    style_mode = top_control_cols[1].selectbox(
        "Style",
        options=STYLE_MODE_OPTIONS,
        key=VIEWER_STYLE_MODE_WIDGET_KEY,
        format_func=lambda value: {
            STYLE_MODE_BALL_AND_STICK: "Ball and Stick",
            STYLE_MODE_STICK: "Stick",
            STYLE_MODE_SPACEFILL: "Spacefill",
        }[value],
    )

    toggle_cols = st.columns(2, gap="medium")
    show_bonds = toggle_cols[0].checkbox(
        "Show bonds",
        key=VIEWER_SHOW_BONDS_WIDGET_KEY,
        disabled=not style_supports_bond_toggle(style_mode),
        help=(
            "Viewer only. PBC-aware bonds use ASE neighbor-list distance heuristics and are off by default."
            if style_supports_bond_toggle(style_mode)
            else "Stick style always shows bonds, and spacefill hides them."
        ),
    )
    show_unit_cell = toggle_cols[1].checkbox("Show unit cell", key=VIEWER_SHOW_UNIT_CELL_WIDGET_KEY)

    visible_species = st.multiselect(
        "Visible species",
        options=available_species,
        key=VIEWER_VISIBLE_SPECIES_WIDGET_KEY,
        help="Viewer only. Use this to focus on selected elements without changing the active structure.",
    )

    with st.expander("Advanced viewer options", expanded=False):
        show_axes = st.checkbox(
            "Show axes",
            key=VIEWER_SHOW_AXES_WIDGET_KEY,
            help="Viewer only. Adds simple Cartesian axes for orientation.",
        )

    effective_show_bonds = get_effective_show_bonds(style_mode, show_bonds)
    settings = ViewerSettings(
        label_mode=label_mode,
        style_mode=style_mode,
        show_bonds=effective_show_bonds,
        show_unit_cell=show_unit_cell,
        show_axes=bool(st.session_state.get(VIEWER_SHOW_AXES_WIDGET_KEY, False)),
        visible_species=tuple(visible_species),
    )

    st.caption("Viewer only: labels, species filters, bonds, axes, and unit-cell outlines affect only the display.")
    if effective_show_bonds:
        st.caption("PBC-aware inspection bonds use ASE neighbor-list distance heuristics.")
    elif style_supports_bond_toggle(style_mode):
        st.caption("PBC-aware inspection bonds are off by default for a cleaner crystal view.")
    if style_mode == STYLE_MODE_STICK:
        st.caption("Stick style always renders bonds in the viewer.")
    elif style_mode == STYLE_MODE_SPACEFILL:
        st.caption("Spacefill emphasizes atom size and hides bonds.")
    return settings


def _render_structure_viewer(ctx: ProjectContext, viewer_settings: ViewerSettings) -> None:
    display_atoms = build_display_atoms(
        ctx.atoms,
        visible_species=viewer_settings.visible_species,
    )
    if display_atoms is None or len(display_atoms) == 0:
        st.info("Select at least one visible species to render the active structure.")
        return

    components.html(
        build_structure_viewer_html(display_atoms, settings=viewer_settings, width="100%", height=620),
        height=620,
    )
    st.caption(
        f"Displayed species: {', '.join(viewer_settings.visible_species)} • Active cell repeat: {format_supercell_repeat(get_structure_supercell_repeat())}"
    )


def _render_structure_metadata(ctx: ProjectContext) -> None:
    if not ctx.structure.loaded:
        return

    with st.expander("Structure Metadata", expanded=False):
        summary_cols = st.columns(3)
        _render_compact_metadata(summary_cols[0], "Formula", ctx.structure.formula or "Unknown")
        _render_compact_metadata(summary_cols[1], "Source", ctx.structure.source_label or "Unknown")
        _render_compact_metadata(summary_cols[2], "Format", (ctx.structure.source_format or "Built").upper() if ctx.structure.source_format else "Built")

        if ctx.structure.cell_parameters:
            st.caption("Cell parameters")
            st.text(ctx.structure.cell_parameters)

        detail_rows = _build_source_detail_rows(ctx.structure.source_details or {})
        if detail_rows:
            st.caption("Source details")
            st.dataframe(detail_rows, use_container_width=True, hide_index=True)


def _build_source_detail_rows(source_details: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, value in source_details.items():
        if value is None or key.startswith("_"):
            continue
        label = key.replace("_", " ").strip().title()
        if isinstance(value, float):
            value_text = f"{value:.6g}"
        else:
            value_text = str(value)
        rows.append({"Field": label, "Value": value_text})
    return rows


def _format_atomic_positions_coordinate_mode_label(mode: str) -> str:
    return {
        "crystal": "Fractional (crystal)",
        "angstrom": "Cartesian (angstrom)",
    }.get(mode, str(mode))


def _commit_structure_result(result, *, payload_bytes: bytes | None = None) -> None:
    commit_structure_source(
        result.atoms,
        source_kind=result.source_kind,
        source_label=result.source_label,
        source_format=result.source_format,
        source_details=result.source_details,
        requested_mode="source",
        payload_bytes=payload_bytes,
    )
    set_structure_error(None)
    if result.warnings:
        st.session_state[ACTIVE_NOTICE_STATE_KEY] = list(result.warnings)
    else:
        st.session_state.pop(ACTIVE_NOTICE_STATE_KEY, None)


def _default_manual_atom_rows() -> list[dict[str, object]]:
    return [
        {"symbol": "", "x": 0.0, "y": 0.0, "z": 0.0},
        {"symbol": "", "x": 0.0, "y": 0.0, "z": 0.0},
    ]


def _draft_widget_key(base_key: str, nonce_state_key: str) -> str:
    nonce = int(st.session_state.get(nonce_state_key, 0))
    return f"{base_key}_{nonce}"


def _increment_draft_nonce(nonce_state_key: str) -> None:
    st.session_state[nonce_state_key] = int(st.session_state.get(nonce_state_key, 0)) + 1


def _reset_file_import_draft() -> None:
    drafts = _get_structure_drafts()
    drafts["file"] = _default_file_draft()
    _store_structure_drafts(drafts)
    _increment_draft_nonce(FILE_DRAFT_NONCE_STATE_KEY)
    set_structure_error(None)


def _reset_manual_builder_draft() -> None:
    drafts = _get_structure_drafts()
    drafts["manual"] = _default_manual_draft()
    _store_structure_drafts(drafts)
    _increment_draft_nonce(MANUAL_DRAFT_NONCE_STATE_KEY)
    set_structure_error(None)


def _prototype_family_nonce_state_key(family_key: str) -> str:
    return f"{PROTOTYPE_DRAFT_NONCE_STATE_KEY}_{family_key}"


def _prototype_family_field_widget_key(family_key: str, field_key: str) -> str:
    return _draft_widget_key(
        f"{PROTOTYPE_FIELD_WIDGET_KEY_PREFIX}_{family_key}_{field_key}",
        _prototype_family_nonce_state_key(family_key),
    )


def _reset_prototype_builder_draft(family_key: str | None = None) -> None:
    drafts = _get_structure_drafts()
    resolved_family_key = family_key or drafts["prototype"]["selected_family"]
    prototype_draft = dict(drafts["prototype"])
    families = dict(prototype_draft["families"])
    families[resolved_family_key] = _normalize_prototype_family_values(
        get_prototype_family(resolved_family_key),
        {},
    )
    prototype_draft["families"] = families
    drafts["prototype"] = prototype_draft
    _store_structure_drafts(drafts)
    _increment_draft_nonce(_prototype_family_nonce_state_key(resolved_family_key))
    set_structure_error(None)
