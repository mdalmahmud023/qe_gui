from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from project_context import (
    ERROR_STATE_KEY,
    ProjectContext,
    STRUCTURE_MODE_OPTIONS,
    apply_structure_mode_selection,
    clear_structure_state,
    commit_structure_source,
    get_structure_mode,
    set_structure_error,
)
from structure_sources import (
    FILE_FORMAT_AUTO,
    FILE_FORMAT_CIF,
    FILE_FORMAT_VASP,
    FILE_FORMAT_XSF,
    FILE_FORMAT_XYZ,
    MANUAL_COORDINATE_MODE_CARTESIAN,
    MANUAL_COORDINATE_MODE_FRACTIONAL,
    build_manual_structure,
    build_ordered_double_perovskite,
    import_structure_file,
)
from structure_viewer import (
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
PROTOTYPE_A_SITE_WIDGET_KEY = "structure_prototype_a_site"
PROTOTYPE_B_SITE_WIDGET_KEY = "structure_prototype_b_site"
PROTOTYPE_B_PRIME_SITE_WIDGET_KEY = "structure_prototype_b_prime_site"
PROTOTYPE_X_SITE_WIDGET_KEY = "structure_prototype_x_site"
PROTOTYPE_A_PARAM_WIDGET_KEY = "structure_prototype_lattice_a"
PROTOTYPE_X_PARAM_WIDGET_KEY = "structure_prototype_x_parameter"
PROTOTYPE_DRAFT_NONCE_STATE_KEY = "structure_prototype_draft_nonce"

ACTIVE_BANNER_CONTAINER_KEY = "structure_active_banner"
DRAFT_PANEL_CONTAINER_KEY = "structure_draft_panel"
INSPECT_PANEL_CONTAINER_KEY = "structure_inspect_panel"


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

        for message_type, message in _collect_active_structure_messages(ctx):
            if message_type == "warning":
                st.warning(message)
            else:
                st.caption(message)


def _render_compact_metadata(column, label: str, value: str) -> None:
    with column:
        st.caption(label)
        st.markdown(f"**{value}**")


def _collect_active_structure_messages(ctx: ProjectContext) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    for notice in st.session_state.get(ACTIVE_NOTICE_STATE_KEY, []) or []:
        if notice:
            messages.append(("info", str(notice)))

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


def _render_structure_draft_panel() -> None:
    st.markdown("#### Import / Build Draft")
    st.caption("Changing draft modes preserves the current active structure. A draft becomes active only after explicit apply.")

    selected_mode = st.radio(
        "Draft Source",
        options=SOURCE_MODE_OPTIONS,
        index=0,
        horizontal=True,
        key=DRAFT_MODE_WIDGET_KEY,
        format_func=lambda value: {
            SOURCE_MODE_FILE: "Import File",
            SOURCE_MODE_MANUAL: "Manual Builder",
            SOURCE_MODE_PROTOTYPE: "Prototype Builder",
        }[value],
    )

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
    selected_format_key = _draft_widget_key(FILE_FORMAT_WIDGET_KEY, FILE_DRAFT_NONCE_STATE_KEY)
    uploaded_file_key = _draft_widget_key(FILE_UPLOADER_KEY, FILE_DRAFT_NONCE_STATE_KEY)
    with st.form("structure_file_import_form"):
        selected_format = st.selectbox(
            "File format",
            options=(FILE_FORMAT_AUTO, FILE_FORMAT_CIF, FILE_FORMAT_VASP, FILE_FORMAT_XSF, FILE_FORMAT_XYZ),
            index=0,
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
        action_col, reset_col = st.columns(2)
        apply_clicked = action_col.form_submit_button("Import and Set Active", use_container_width=True)
        reset_clicked = reset_col.form_submit_button("Reset Draft", use_container_width=True)

    if reset_clicked:
        _reset_file_import_draft()
        st.rerun()
        return
    if not apply_clicked:
        return
    if uploaded_file is None:
        set_structure_error("Select a structure file to import.")
        st.rerun()
        return

    try:
        file_bytes = uploaded_file.getvalue()
        result = import_structure_file(
            filename=uploaded_file.name,
            file_bytes=file_bytes,
            requested_format=selected_format,
        )
        _commit_structure_result(result, payload_bytes=file_bytes)
    except Exception as exc:
        set_structure_error(f"Failed to import the structure file: {exc}")
    st.rerun()


def _render_manual_builder_mode() -> None:
    st.caption("Draft only until applied. Enter a periodic crystal source cell using lattice parameters plus an atom table.")
    formula_key = _draft_widget_key(MANUAL_FORMULA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    a_key = _draft_widget_key(MANUAL_A_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    b_key = _draft_widget_key(MANUAL_B_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    c_key = _draft_widget_key(MANUAL_C_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    alpha_key = _draft_widget_key(MANUAL_ALPHA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    beta_key = _draft_widget_key(MANUAL_BETA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    gamma_key = _draft_widget_key(MANUAL_GAMMA_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    coordinate_mode_key = _draft_widget_key(MANUAL_COORDINATE_MODE_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    atom_table_key = _draft_widget_key(MANUAL_ATOM_TABLE_WIDGET_KEY, MANUAL_DRAFT_NONCE_STATE_KEY)
    with st.form("structure_manual_builder_form"):
        formula_text = st.text_input(
            "Formula (optional validation only)",
            key=formula_key,
            help="Use this only to validate the atom table. The structure is built from the rows below, not from the formula text.",
        )

        row_one_col1, row_one_col2, row_one_col3 = st.columns(3)
        row_one_col1.number_input("a (A)", min_value=0.0, value=6.0, step=0.1, key=a_key)
        row_one_col2.number_input("b (A)", min_value=0.0, value=6.0, step=0.1, key=b_key)
        row_one_col3.number_input("c (A)", min_value=0.0, value=6.0, step=0.1, key=c_key)

        row_two_col1, row_two_col2, row_two_col3 = st.columns(3)
        row_two_col1.number_input("alpha (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=alpha_key)
        row_two_col2.number_input("beta (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=beta_key)
        row_two_col3.number_input("gamma (deg)", min_value=0.0, max_value=180.0, value=90.0, step=1.0, key=gamma_key)

        st.radio(
            "Coordinate mode",
            options=(MANUAL_COORDINATE_MODE_FRACTIONAL, MANUAL_COORDINATE_MODE_CARTESIAN),
            horizontal=True,
            key=coordinate_mode_key,
            format_func=lambda value: "Fractional" if value == MANUAL_COORDINATE_MODE_FRACTIONAL else "Cartesian",
        )
        atom_rows = st.data_editor(
            _default_manual_atom_rows(),
            key=atom_table_key,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
        )
        action_col, reset_col = st.columns(2)
        apply_clicked = action_col.form_submit_button("Build and Set Active", use_container_width=True)
        reset_clicked = reset_col.form_submit_button("Reset Draft", use_container_width=True)

    if reset_clicked:
        _reset_manual_builder_draft()
        st.rerun()
        return
    if not apply_clicked:
        return

    try:
        result = build_manual_structure(
            a=float(st.session_state.get(a_key, 6.0)),
            b=float(st.session_state.get(b_key, 6.0)),
            c=float(st.session_state.get(c_key, 6.0)),
            alpha=float(st.session_state.get(alpha_key, 90.0)),
            beta=float(st.session_state.get(beta_key, 90.0)),
            gamma=float(st.session_state.get(gamma_key, 90.0)),
            coordinate_mode=st.session_state.get(coordinate_mode_key, MANUAL_COORDINATE_MODE_FRACTIONAL),
            atom_rows=atom_rows,
            formula_text=formula_text,
        )
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the manual structure: {exc}")
    st.rerun()


def _render_prototype_builder_mode() -> None:
    st.caption("Draft only until applied. Build an ordered cubic double perovskite / elpasolite source cell for `A2BB'X6`.")
    a_site_key = _draft_widget_key(PROTOTYPE_A_SITE_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    x_site_key = _draft_widget_key(PROTOTYPE_X_SITE_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    b_site_key = _draft_widget_key(PROTOTYPE_B_SITE_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    b_prime_site_key = _draft_widget_key(PROTOTYPE_B_PRIME_SITE_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    lattice_parameter_key = _draft_widget_key(PROTOTYPE_A_PARAM_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    x_parameter_key = _draft_widget_key(PROTOTYPE_X_PARAM_WIDGET_KEY, PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    with st.form("structure_prototype_builder_form"):
        row_one_col1, row_one_col2 = st.columns(2)
        row_one_col1.text_input("A site", value="Na", key=a_site_key)
        row_one_col2.text_input("X site", value="H", key=x_site_key)

        row_two_col1, row_two_col2 = st.columns(2)
        row_two_col1.text_input("B site", value="Ca", key=b_site_key)
        row_two_col2.text_input("B' site", value="Cd", key=b_prime_site_key)

        st.number_input(
            "Cubic lattice parameter a (A)",
            min_value=0.0,
            value=8.0,
            step=0.1,
            key=lattice_parameter_key,
        )
        with st.expander("Advanced prototype options", expanded=False):
            st.number_input(
                "Anion Wyckoff x parameter",
                min_value=0.0,
                max_value=0.5,
                value=0.25,
                step=0.01,
                key=x_parameter_key,
                help="The ideal cubic double-perovskite value is 0.25.",
            )
        action_col, reset_col = st.columns(2)
        apply_clicked = action_col.form_submit_button("Build and Set Active", use_container_width=True)
        reset_clicked = reset_col.form_submit_button("Reset Draft", use_container_width=True)

    if reset_clicked:
        _reset_prototype_builder_draft()
        st.rerun()
        return
    if not apply_clicked:
        return

    try:
        result = build_ordered_double_perovskite(
            a_site=st.session_state.get(a_site_key, "Na"),
            b_site=st.session_state.get(b_site_key, "Ca"),
            b_prime_site=st.session_state.get(b_prime_site_key, "Cd"),
            x_site=st.session_state.get(x_site_key, "H"),
            lattice_parameter=float(st.session_state.get(lattice_parameter_key, 8.0)),
            x_parameter=float(st.session_state.get(x_parameter_key, 0.25)),
        )
        _commit_structure_result(result)
    except Exception as exc:
        set_structure_error(f"Failed to build the prototype structure: {exc}")
    st.rerun()


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

    status_bits: list[str] = []
    if ctx.structure.mode_status:
        if "failed" in ctx.structure.mode_status.lower():
            st.warning(ctx.structure.mode_status)
        elif ctx.structure.mode_status not in {"Using the source cell.", "The source structure already looks like a primitive cell."}:
            status_bits.append(ctx.structure.mode_status)
    status_bits.append("Changing the active cell representation affects newly generated QE inputs. Regenerate `pw.in` before DOS or bands.")
    st.caption(" ".join(status_bits))


def _render_structure_viewer_controls(ctx: ProjectContext) -> ViewerSettings:
    st.markdown("##### Visualization Settings")
    available_species = get_sorted_species(ctx.atoms)
    normalized_species = normalize_visible_species(ctx.atoms, st.session_state.get(VIEWER_VISIBLE_SPECIES_WIDGET_KEY))
    st.session_state[VIEWER_VISIBLE_SPECIES_WIDGET_KEY] = list(normalized_species)
    st.session_state.setdefault(VIEWER_LABEL_MODE_WIDGET_KEY, LABEL_MODE_OFF)
    st.session_state.setdefault(VIEWER_STYLE_MODE_WIDGET_KEY, STYLE_MODE_BALL_AND_STICK)
    st.session_state.setdefault(VIEWER_SHOW_BONDS_WIDGET_KEY, True)
    st.session_state.setdefault(VIEWER_SHOW_UNIT_CELL_WIDGET_KEY, True)
    st.session_state.setdefault(VIEWER_SHOW_AXES_WIDGET_KEY, False)
    st.session_state.setdefault(VIEWER_SUPERCELL_MODE_WIDGET_KEY, "preset_111")
    st.session_state.setdefault(VIEWER_SUPERCELL_A_WIDGET_KEY, 1)
    st.session_state.setdefault(VIEWER_SUPERCELL_B_WIDGET_KEY, 1)
    st.session_state.setdefault(VIEWER_SUPERCELL_C_WIDGET_KEY, 1)

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
            "Viewer only. Bonds are heuristic for inspection."
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
        supercell_mode = st.selectbox(
            "Supercell repeat",
            options=SUPERCELL_MODE_OPTIONS,
            key=VIEWER_SUPERCELL_MODE_WIDGET_KEY,
            format_func=lambda value: {
                "preset_111": "1x1x1",
                "preset_211": "2x1x1",
                "preset_222": "2x2x2",
                SUPERCELL_MODE_CUSTOM: "Custom",
            }[value],
        )
        if supercell_mode == SUPERCELL_MODE_CUSTOM:
            repeat_cols = st.columns(3)
            repeat_cols[0].number_input("Repeat a", min_value=1, max_value=3, step=1, key=VIEWER_SUPERCELL_A_WIDGET_KEY)
            repeat_cols[1].number_input("Repeat b", min_value=1, max_value=3, step=1, key=VIEWER_SUPERCELL_B_WIDGET_KEY)
            repeat_cols[2].number_input("Repeat c", min_value=1, max_value=3, step=1, key=VIEWER_SUPERCELL_C_WIDGET_KEY)
            supercell_repeat = get_supercell_repeat_for_mode(
                supercell_mode,
                custom_repeat=(
                    st.session_state.get(VIEWER_SUPERCELL_A_WIDGET_KEY, 1),
                    st.session_state.get(VIEWER_SUPERCELL_B_WIDGET_KEY, 1),
                    st.session_state.get(VIEWER_SUPERCELL_C_WIDGET_KEY, 1),
                ),
            )
        else:
            supercell_repeat = get_supercell_repeat_for_mode(supercell_mode)

    effective_show_bonds = get_effective_show_bonds(style_mode, show_bonds)
    settings = ViewerSettings(
        label_mode=label_mode,
        style_mode=style_mode,
        show_bonds=effective_show_bonds,
        show_unit_cell=show_unit_cell,
        show_axes=bool(st.session_state.get(VIEWER_SHOW_AXES_WIDGET_KEY, False)),
        visible_species=tuple(visible_species),
        supercell_repeat=supercell_repeat,
    )

    st.caption("Viewer only: labels, species filters, bonds, and supercell repeats affect only the display.")
    if effective_show_bonds:
        st.caption("Heuristic bonds for inspection only.")
    if settings.supercell_repeat != (1, 1, 1):
        st.caption(
            f"Viewer only supercell repeat: {format_supercell_repeat(settings.supercell_repeat)}. QE Studio still uses the original active structure downstream."
        )
    if style_mode == STYLE_MODE_STICK:
        st.caption("Stick style always renders bonds in the viewer.")
    elif style_mode == STYLE_MODE_SPACEFILL:
        st.caption("Spacefill emphasizes atom size and hides bonds.")
    return settings


def _render_structure_viewer(ctx: ProjectContext, viewer_settings: ViewerSettings) -> None:
    display_atoms = build_display_atoms(
        ctx.atoms,
        visible_species=viewer_settings.visible_species,
        supercell_repeat=viewer_settings.supercell_repeat,
    )
    if display_atoms is None or len(display_atoms) == 0:
        st.info("Select at least one visible species to render the active structure.")
        return

    components.html(
        build_structure_viewer_html(display_atoms, settings=viewer_settings, width="100%", height=620),
        height=620,
    )
    st.caption(
        f"Displayed species: {', '.join(viewer_settings.visible_species)} • Displayed cell: {format_supercell_repeat(viewer_settings.supercell_repeat)}"
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
        if value is None:
            continue
        label = key.replace("_", " ").strip().title()
        if isinstance(value, float):
            value_text = f"{value:.6g}"
        else:
            value_text = str(value)
        rows.append({"Field": label, "Value": value_text})
    return rows


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
    _increment_draft_nonce(FILE_DRAFT_NONCE_STATE_KEY)
    set_structure_error(None)


def _reset_manual_builder_draft() -> None:
    _increment_draft_nonce(MANUAL_DRAFT_NONCE_STATE_KEY)
    set_structure_error(None)


def _reset_prototype_builder_draft() -> None:
    _increment_draft_nonce(PROTOTYPE_DRAFT_NONCE_STATE_KEY)
    set_structure_error(None)
