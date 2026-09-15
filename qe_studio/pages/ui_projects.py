"""Phase 1 project manager and identity-only overview."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ..core.project_models import Project, ProjectConflictError, ProjectError
from ..core.project_session import LEGACY, MANAGER, PREFIX, request_context, widget_key
from ..services.project_service import create_project, open_project, rename_project
from ..storage.workspace_store import WorkspaceStore


def _remember(project: Project, store: WorkspaceStore) -> None:
    try:
        store.remember(project)
    except ProjectConflictError:
        raise
    except ProjectError as exc:
        # The disposable index must not make an otherwise valid project unusable.
        st.session_state[PREFIX + "notice"] = str(exc)


def _select(project: Project, store: WorkspaceStore) -> None:
    _remember(project, store)
    request_context(st.session_state, project.id, str(project.root_path))
    st.rerun()


def render_manager() -> None:
    st.title("Projects")
    st.caption("Create or open a local project. Projects support structure history and baselines. Calculations remain in Legacy Workspace.")
    st.button("Legacy Workspace", key=PREFIX + "legacy", on_click=request_context,
              args=(st.session_state, LEGACY))
    store = WorkspaceStore()
    with st.expander("Create Project", expanded=True):
        with st.form(PREFIX + "create"):
            name = st.text_input("Display name", key=PREFIX + "create_name")
            parent = st.text_input("Parent directory", value=str(Path.home() / "QEStudioProjects"), key=PREFIX + "create_parent")
            description = st.text_area("Description", key=PREFIX + "create_description")
            submitted = st.form_submit_button("Create Project")
        if submitted:
            try:
                _select(create_project(parent, name, description), store)
            except ProjectError as exc:
                st.error(str(exc))

    with st.expander("Open Existing", expanded=True):
        with st.form(PREFIX + "open"):
            root = st.text_input("Project directory on this computer", key=PREFIX + "open_root")
            submitted = st.form_submit_button("Open Project")
        if submitted:
            try:
                _select(open_project(root), store)
            except ProjectError as exc:
                st.error(str(exc))

    st.subheader("Recent Projects")
    try:
        recents = store.list_recent()
    except ProjectError as exc:
        st.warning(str(exc))
        recents = []
    if not recents:
        st.caption("No recent projects.")
    for recent in recents:
        st.write(recent.display_name)
        st.caption(f"{recent.root_path} · {recent.project_id} · Last opened {recent.last_opened_at:%Y-%m-%d %H:%M UTC}")
        if not recent.available:
            st.caption("Location missing. Use Open Existing to locate a moved project.")
        if st.button("Open", key=PREFIX + "recent_" + recent.project_id, disabled=not recent.available):
            try:
                project = open_project(recent.root_path)
                if project.id != recent.project_id:
                    raise ProjectError("This location now contains a different project. Use Open Existing to open it explicitly.")
                _select(project, store)
            except ProjectError as exc:
                st.error(str(exc))


def render_overview(ctx) -> None:
    project = ctx.project
    st.title(project.display_name)
    if project.description:
        st.write(project.description)
    st.caption("Project overview · Structure history and scientific baselines")
    st.write("Project UUID", project.id)
    st.write("Location", str(project.root_path))
    st.write("Created", project.created_at.isoformat())
    st.write("Modified", project.modified_at.isoformat())
    st.write("Schema version", str(project.schema_version))

    st.write("Active structure", project.active_structure_id or "None")
    st.write("Active baseline", project.active_baseline_id or "None")
    if ctx.persistence_error:
        st.error(ctx.persistence_error)
    from ..storage.project_repository import pending_payloads
    pending = pending_payloads(project)
    if pending:
        st.warning("Unregistered payloads from interrupted writes are preserved: " + ", ".join(pending))

    snapshot_key = widget_key(project.id, "edit_snapshot")
    snapshot = st.session_state.setdefault(snapshot_key, project)
    with st.expander("Rename / Edit Project"):
        with st.form(widget_key(project.id, "rename")):
            revision = snapshot.modified_at.isoformat()
            name = st.text_input("Display name", value=snapshot.display_name, key=widget_key(project.id, "name:" + revision))
            description = st.text_area("Description", value=snapshot.description, key=widget_key(project.id, "description:" + revision))
            submitted = st.form_submit_button("Save Project Details")
        if submitted:
            try:
                updated = rename_project(snapshot, name, description=description)
                st.session_state[snapshot_key] = updated
                _remember(updated, WorkspaceStore())
                st.rerun()
            except ProjectError as exc:
                st.error(str(exc))
        st.button("Reload Project Details", key=widget_key(project.id, "reload"),
                  on_click=st.session_state.pop, args=(snapshot_key, None))


def render_context_controls(project: Project | None = None, *, legacy: bool = False) -> None:
    with st.sidebar:
        st.caption(project.display_name if project else "Legacy Workspace" if legacy else "Project unavailable")
        st.button("Projects", key=PREFIX + "projects", on_click=request_context,
                  args=(st.session_state, MANAGER))
        st.button("Close Legacy Workspace" if legacy else "Close Project", key=PREFIX + "close", on_click=request_context,
                  args=(st.session_state, MANAGER))
        if not legacy:
            st.button("Legacy Workspace", key=PREFIX + "legacy", on_click=request_context,
                      args=(st.session_state, LEGACY))
