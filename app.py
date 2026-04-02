from __future__ import annotations

import streamlit as st

from project_context import QE_RUNS_DIR, build_project_context, initialize_job_summaries_state
from qe_runner import initialize_run_state
import ui_analysis
import ui_run
import ui_setup
import ui_structure


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
            st.caption(f"{ctx.structure.cell_mode_label} • {ctx.structure.atom_count} atoms")
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


def render_workflow_indicator(active_title: str) -> None:
    """Render a compact workflow indicator in the shared app frame."""
    st.caption("Workflow")
    steps = ("Structure", "Setup", "Run", "Analysis")
    columns = st.columns(len(steps))
    for column, title in zip(columns, steps):
        is_active = title == active_title
        background = "#1b3558" if is_active else "#1b2230"
        border = "#7aa2ff" if is_active else "#46506a"
        title_color = "#f7f9fc"
        subtitle_color = "#d6deeb" if is_active else "#b6c0d4"
        subtitle = "Current step" if is_active else "&nbsp;"
        column.markdown(
            (
                "<div style='border:1px solid {border};border-radius:0.75rem;padding:0.65rem 0.8rem;"
                "background:{background};text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.18);'>"
                "<div style='font-weight:600;color:{title_color};'>{title}</div>"
                "<div style='font-size:0.78rem;color:{subtitle_color};'>{subtitle}</div>"
                "</div>"
            ).format(
                border=border,
                background=background,
                title_color=title_color,
                subtitle_color=subtitle_color,
                title=title,
                subtitle=subtitle,
            ),
            unsafe_allow_html=True,
        )


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
