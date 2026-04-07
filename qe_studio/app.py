from __future__ import annotations

import streamlit as st

from .core.project_context import build_project_context, initialize_job_summaries_state
from .core.workspace import QE_RUNS_DIR
from .pages import ui_analysis, ui_run, ui_setup, ui_structure
from .services.qe_runner import initialize_run_state


def render_app_shell_styles() -> None:
    """Apply small layout tweaks for the shared Streamlit shell."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
            padding-top: 0.1rem;
            padding-bottom: 0.1rem;
            min-height: 0;
        }
        [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
            padding-top: 0 !important;
            margin-top: -2.05rem;
        }
        [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] > div:first-child {
            padding-top: 0 !important;
        }
        [data-testid="stSidebar"] h1 {
            margin-top: 0;
            padding-top: 0;
            margin-bottom: 0.35rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_project_summary(ctx) -> None:
    """Render the shared sidebar status frame."""
    with st.sidebar:
        st.title("QE Studio")
        st.caption("Quantum ESPRESSO workspace")

        st.divider()
        st.caption("Workspace")
        st.write(str(ctx.run_dir))

        st.caption("Structure")
        if ctx.structure.loaded:
            st.write(ctx.structure.formula or "Unknown")
            structure_bits = [ctx.structure.cell_mode_label, f"{ctx.structure.atom_count} atoms"]
            if ctx.structure.source_kind_label:
                structure_bits.append(ctx.structure.source_kind_label)
            st.caption(" • ".join(structure_bits))
            if ctx.structure.source_label:
                st.caption(ctx.structure.source_label)
        else:
            st.write("No structure loaded")

        st.caption("Pseudopotentials")
        st.write(ctx.pseudopotentials.status_label)
        if ctx.pseudopotentials.message:
            st.caption(ctx.pseudopotentials.message)

        st.caption("Input")
        st.write(ctx.input_files.status_label)
        if ctx.input_files.available:
            st.caption(str(ctx.input_files.ui_path))
        elif ctx.input_files.blocker_message:
            st.caption(ctx.input_files.blocker_message)
        if ctx.input_files.provenance_message:
            st.caption(ctx.input_files.provenance_message)

        st.caption("Run Status")
        st.write(ctx.jobs.latest_status_label)
        if ctx.jobs.latest_status_detail:
            st.caption(ctx.jobs.latest_status_detail)

        st.caption("Artifacts")
        artifact_flags = [
            f"DOS: {'Yes' if ctx.artifacts.dos_detected else 'No'}",
            f"Bands: {'Yes' if ctx.artifacts.bands_detected else 'No'}",
            f"Metadata: {'Yes' if ctx.artifacts.metadata_detected else 'No'}",
        ]
        st.caption(" • ".join(artifact_flags))
        if ctx.artifacts.provenance_message:
            st.caption(ctx.artifacts.provenance_message)


def render_workflow_indicator(active_title: str) -> None:
    """Render a lightweight workflow indicator in the shared app frame."""
    st.caption("Workflow")
    steps = ("Structure", "Setup", "Run", "Analysis")
    columns = st.columns(len(steps))
    for column, title in zip(columns, steps):
        is_active = title == active_title
        background = "rgba(59, 130, 246, 0.15)" if is_active else "rgba(148, 163, 184, 0.10)"
        border = "rgba(59, 130, 246, 0.45)" if is_active else "rgba(100, 116, 139, 0.35)"
        title_color = "#e2e8f0" if is_active else "#cbd5e1"
        subtitle = "Active" if is_active else "Step"
        column.markdown(
            (
                "<div style='border:1px solid {border};border-radius:999px;padding:0.4rem 0.8rem;"
                "background:{background};text-align:center;'>"
                "<div style='font-size:0.74rem;letter-spacing:0.02em;text-transform:uppercase;color:{title_color};'>{subtitle}</div>"
                "<div style='font-weight:600;color:{title_color};'>{title}</div>"
                "</div>"
            ).format(
                border=border,
                background=background,
                title_color=title_color,
                title=title,
                subtitle=subtitle,
            ),
            unsafe_allow_html=True,
        )


def main() -> None:
    st.set_page_config(page_title="QE Studio", layout="wide")
    render_app_shell_styles()
    initialize_run_state(st.session_state)
    initialize_job_summaries_state()
    ctx = build_project_context(QE_RUNS_DIR)

    def render_structure_page() -> None:
        ui_structure.render_page(ctx)

    def render_setup_page() -> None:
        ui_setup.render_page(ctx)

    def render_run_page() -> None:
        ui_run.render_page(ctx)

    def render_analysis_page() -> None:
        ui_analysis.render_page(ctx)

    pages = [
        st.Page(render_structure_page, title="Structure", url_path="structure", default=True),
        st.Page(render_setup_page, title="Setup", url_path="setup"),
        st.Page(render_run_page, title="Run", url_path="run"),
        st.Page(render_analysis_page, title="Analysis", url_path="analysis"),
    ]
    selected_page = st.navigation(pages, position="top")

    render_sidebar_project_summary(ctx)
    render_workflow_indicator(selected_page.title)
    selected_page.run()


if __name__ == "__main__":
    main()
