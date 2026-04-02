from __future__ import annotations

import shutil
from pathlib import Path

import streamlit as st

from postprocessing import (
    build_bands_pp_input_text,
    build_dos_input_text,
    get_effective_band_nbnd,
    resolve_qe_run_context,
    sanitize_leaf_filename,
    summarize_artifacts,
    transform_input_to_bands_pw,
    validate_band_output_file,
    validate_bands_prerequisites,
    validate_dos_output_file,
    validate_dos_prerequisites,
)
from project_context import (
    AUTO_BAND_PATH_POINTS_WIDGET_KEY,
    AUTO_BAND_PATH_STATUS_STATE_KEY,
    BAND_COORDINATE_MODE_OPTIONS,
    BAND_COORDINATE_MODE_WIDGET_KEY,
    BAND_NBND_WIDGET_KEY,
    BAND_PATH_WIDGET_KEY,
    BANDS_PP_COMMAND,
    BANDS_PP_INPUT_FILENAME,
    BANDS_PP_OUTPUT_FILENAME,
    BANDS_PW_COMMAND,
    BANDS_PW_INPUT_FILENAME,
    BANDS_PW_OUTPUT_FILENAME,
    DEFAULT_BAND_PATH_TEXT,
    DOS_BZ_SUM_OPTIONS,
    DOS_COMMAND,
    DOS_INPUT_FILENAME,
    DOS_OUTPUT_FILENAME,
    FILBAND_FILENAME,
    FILBAND_GNU_FILENAME,
    JOB_OWNER_BANDS,
    JOB_OWNER_DOS,
    JOB_OWNER_PW,
    ProjectContext,
    QE_OUTPUT_FILENAME,
    QE_RUN_COMMAND,
    QE_RUNS_DIR,
    get_active_job_label,
    get_active_job_owner,
    get_job_summary,
    get_structure_mode,
    sync_active_job_summary_from_run_state,
    update_job_summary,
)
from qe_runner import (
    ACTIVE_RUN_ID_STATE_KEY,
    DEFAULT_MAX_TAIL_LINES,
    RUN_ACTIVE_STATE_KEY,
    RUN_FINISHED_STATE_KEY,
    RUN_JOB_LABEL_STATE_KEY,
    RUN_JOB_OWNER_STATE_KEY,
    RUN_OUTPUT_PATH_STATE_KEY,
    RUN_PHASE_LABEL_STATE_KEY,
    RUN_RETURNCODE_STATE_KEY,
    RUN_TAIL_LINES_STATE_KEY,
    JobStep,
    apply_run_events,
    cleanup_run,
    drain_run_events,
    get_run_context,
    mark_runtime_lost,
    reset_run_state,
    run_chained_jobs,
    run_external_job,
    set_run_started,
)
from structure_tools import generate_band_path_rows


def render_page(ctx: ProjectContext) -> None:
    """Render the Run page."""
    st.subheader("Run")
    st.caption("Launch local Quantum ESPRESSO jobs, manage post-processing workflows, and monitor operational output.")

    if ctx.jobs.run_active and ctx.jobs.latest_status_detail:
        st.info(f"Active job: `{ctx.jobs.latest_status_detail}`. Live console output is available below.")

    render_artifact_status_table(ctx)
    st.divider()
    render_qe_run_section(ctx)
    st.divider()
    st.markdown("### Post-Processing")
    st.caption("Run follow-up DOS and band-structure workflows using the saved QE data under `qe_runs/`.")
    render_dos_runner_subsection()
    st.divider()
    render_band_workflow_subsection(ctx)


def render_artifact_status_table(ctx: ProjectContext) -> None:
    """Render a lightweight table of key workspace artifacts."""
    rows = [
        {"Artifact": "pw.in", "Path": str(ctx.input_files.ui_path), "Present": "Yes" if ctx.input_files.ui_path.is_file() else "No"},
        {"Artifact": "pw.out", "Path": str(ctx.run_dir / QE_OUTPUT_FILENAME), "Present": "Yes" if (ctx.run_dir / QE_OUTPUT_FILENAME).is_file() else "No"},
        {"Artifact": "DOS data", "Path": str(ctx.artifacts.dos_file) if ctx.artifacts.dos_file else "Unavailable", "Present": "Yes" if ctx.artifacts.dos_detected else "No"},
        {"Artifact": "filband.gnu", "Path": str(ctx.artifacts.band_file) if ctx.artifacts.band_file else "Unavailable", "Present": "Yes" if ctx.artifacts.bands_detected else "No"},
    ]
    st.markdown("### Artifacts")
    st.dataframe(rows, use_container_width=True, hide_index=True)


def format_run_output_text() -> str:
    """Return the current rolling terminal tail for display."""
    tail_lines = st.session_state.get(RUN_TAIL_LINES_STATE_KEY, [])
    if tail_lines:
        return "".join(tail_lines)
    phase_label = st.session_state.get(RUN_PHASE_LABEL_STATE_KEY)
    job_label = st.session_state.get(RUN_JOB_LABEL_STATE_KEY)
    wait_label = phase_label or job_label or "Quantum ESPRESSO"
    if st.session_state.get(RUN_ACTIVE_STATE_KEY):
        return f"Waiting for {wait_label} output...\n"
    if st.session_state.get(RUN_FINISHED_STATE_KEY):
        return f"{wait_label} finished without terminal output.\n"
    return "Run output will appear here.\n"


@st.fragment(run_every=1)
def render_live_run_output_fragment() -> None:
    """Poll the active run queue and repaint the live output tail."""
    output_placeholder = st.empty()
    active_run_id = st.session_state.get(ACTIVE_RUN_ID_STATE_KEY)

    if not active_run_id:
        mark_runtime_lost(st.session_state)
        sync_active_job_summary_from_run_state()
        output_placeholder.code(format_run_output_text(), language="text")
        st.rerun(scope="app")
        return

    context = get_run_context(active_run_id)
    if context is None:
        mark_runtime_lost(st.session_state)
        sync_active_job_summary_from_run_state()
        output_placeholder.code(format_run_output_text(), language="text")
        st.rerun(scope="app")
        return

    events = drain_run_events(active_run_id)
    run_completed = apply_run_events(
        st.session_state,
        events,
        max_tail_lines=DEFAULT_MAX_TAIL_LINES,
    )
    sync_active_job_summary_from_run_state()
    output_placeholder.code(format_run_output_text(), language="text")

    if not run_completed and st.session_state.get(RUN_ACTIVE_STATE_KEY) and not context.thread.is_alive():
        mark_runtime_lost(st.session_state)
        sync_active_job_summary_from_run_state()
        cleanup_run(active_run_id)
        st.rerun(scope="app")
        return

    if run_completed:
        cleanup_run(active_run_id)
        sync_active_job_summary_from_run_state()
        st.rerun(scope="app")


def render_job_status_block(owner: str, *, idle_message: str) -> None:
    """Render a compact status area for one QE job section."""
    summary = get_job_summary(owner)
    active_owner = get_active_job_owner()
    active_label = get_active_job_label()

    if active_owner and active_owner != owner and active_label:
        st.info(f"`{active_label}` is currently running. Wait for it to finish before starting another QE job.")

    status = summary.get("status")
    current_phase = summary.get("current_phase")
    returncode = summary.get("returncode")
    job_label = summary.get("job_label") or idle_message

    if status == "running":
        phase_suffix = f" Current phase: `{current_phase}`." if current_phase else ""
        st.info(f"{job_label} is running.{phase_suffix}")
    elif status == "success":
        st.success(f"{job_label} finished successfully with exit code {returncode}.")
    elif status == "error":
        st.error(f"{job_label} exited with code {returncode}.")
    else:
        st.info(idle_message)


def render_job_console(owner: str, *, empty_message: str) -> None:
    """Render either the live console or the last stored tail for one section."""
    if st.session_state.get(RUN_ACTIVE_STATE_KEY) and get_active_job_owner() == owner:
        render_live_run_output_fragment()
        return

    summary = get_job_summary(owner)
    tail_lines = summary.get("tail_lines") or []
    if tail_lines:
        st.code("".join(tail_lines), language="text")
    else:
        st.code(f"{empty_message}\n", language="text")


def render_job_output_panel(owner: str, *, title: str, empty_message: str) -> None:
    """Render a collapsible console panel with automatic expansion for active jobs."""
    expanded = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY) and get_active_job_owner() == owner)
    with st.expander(title, expanded=expanded):
        render_job_console(owner, empty_message=empty_message)


def render_job_log_paths(owner: str) -> None:
    """Show persisted log paths for a section summary."""
    summary = get_job_summary(owner)
    log_paths = summary.get("log_paths") or {}
    for label, path in log_paths.items():
        st.caption(f"{label}: `{path}`")


def render_job_artifact_summary(owner: str) -> None:
    """Render an artifact table after a successful run."""
    summary = get_job_summary(owner)
    if summary.get("status") != "success":
        return

    artifacts = summary.get("artifacts") or {}
    if artifacts:
        st.dataframe(
            summarize_artifacts({label: Path(path) for label, path in artifacts.items()}),
            use_container_width=True,
            hide_index=True,
        )


def start_managed_qe_job(
    *,
    owner: str,
    job_label: str,
    steps: list[JobStep],
    log_paths: dict[str, str],
    artifacts: dict[str, str],
) -> None:
    """Start a reusable QE job and store its durable UI summary."""
    if st.session_state.get(RUN_ACTIVE_STATE_KEY):
        active_label = get_active_job_label() or "Another Quantum ESPRESSO job"
        raise RuntimeError(f"{active_label} is already running.")

    reset_run_state(st.session_state)
    if len(steps) == 1:
        run_id, output_paths = run_external_job(
            job_label=steps[0].label,
            command=steps[0].command,
            cwd=steps[0].cwd,
            output_path=steps[0].output_path,
            prepare=steps[0].prepare,
            validator=steps[0].validator,
        )
    else:
        run_id, output_paths = run_chained_jobs(job_label=job_label, steps=steps)

    output_path_map = {step.label: str(path) for step, path in zip(steps, output_paths)}
    set_run_started(
        st.session_state,
        run_id=run_id,
        output_path=steps[0].output_path,
        output_paths=output_path_map,
        job_label=job_label,
        job_owner=owner,
        phase_label=steps[0].label,
    )
    update_job_summary(
        owner,
        status="running",
        job_label=job_label,
        current_phase=steps[0].label,
        last_phase=steps[0].label,
        returncode=None,
        log_paths=log_paths,
        artifacts=artifacts,
        tail_lines=[],
        current_output_path=str(steps[0].output_path),
    )
    st.rerun()


def render_saved_run_context(context) -> None:
    """Render resolved disk-first QE context for post-processing."""
    col1, col2, col3, col4 = st.columns(4)
    col1.caption("Source input")
    col1.write(str(context.source_input_file) if context.source_input_file else "Unavailable")
    col2.caption("Prefix")
    col2.write(context.prefix)
    col3.caption("Outdir")
    col3.write(context.effective_outdir)
    col4.caption("Save dir")
    col4.write(str(context.save_dir) if context.save_dir else "Missing")

    col5, col6, col7 = st.columns(3)
    col5.caption("Calculation")
    col5.write(context.calculation or "Unknown")
    col6.caption("K_POINTS")
    col6.write(context.k_points_mode or "Unknown")
    col7.caption("XML metadata")
    col7.write("Yes" if context.has_xml_metadata else "No")

    col8, col9 = st.columns(2)
    col8.caption("Restart wavefunctions")
    col8.write("Yes" if context.has_restart_wavefunctions else "No")
    col9.caption("Band-count estimate")
    if context.band_count_estimate is None:
        col9.write("Unavailable")
    else:
        col9.write(
            f"min {context.band_count_estimate.minimum_nbnd}, "
            f"recommended {context.band_count_estimate.recommended_nbnd}"
        )


def render_prerequisite_feedback(check) -> None:
    """Render blockers and warnings for a prerequisite check."""
    for blocker in check.blockers:
        st.error(blocker)
    for warning in check.warnings:
        st.warning(warning)
    if check.ok and not check.warnings:
        st.success("Prerequisites are satisfied.")


def render_auto_band_path_controls(ctx: ProjectContext) -> None:
    """Offer auto-generated high-symmetry paths from the active structure."""
    if ctx.atoms is None:
        st.caption("Load a structure on the Structure page to auto-generate a band path.")
        return

    auto_points_col, auto_button_col = st.columns([1, 2])
    points_per_segment = auto_points_col.number_input(
        "Auto path points/segment",
        min_value=1,
        value=int(st.session_state.get(AUTO_BAND_PATH_POINTS_WIDGET_KEY, 20)),
        step=1,
        key=AUTO_BAND_PATH_POINTS_WIDGET_KEY,
    )
    auto_fill_clicked = auto_button_col.button(
        "Auto-Fill High-Symmetry Path",
        use_container_width=True,
    )

    if auto_fill_clicked:
        try:
            auto_path = generate_band_path_rows(ctx.atoms, points_per_segment=int(points_per_segment))
        except Exception as exc:
            st.session_state[AUTO_BAND_PATH_STATUS_STATE_KEY] = {
                "state": "error",
                "message": f"Failed to auto-generate a band path: {exc}",
            }
        else:
            st.session_state[BAND_COORDINATE_MODE_WIDGET_KEY] = "crystal_b"
            st.session_state[BAND_PATH_WIDGET_KEY] = auto_path.rows_text
            message = f"Auto-filled ASE path `{auto_path.path_string}` using the active structure."
            if auto_path.used_first_section_only:
                message += " Only the first continuous segment was filled automatically; you can extend it manually."
            st.session_state[AUTO_BAND_PATH_STATUS_STATE_KEY] = {
                "state": "success",
                "message": message,
            }
            st.rerun()

    status = st.session_state.get(AUTO_BAND_PATH_STATUS_STATE_KEY)
    if status:
        if status.get("state") == "success":
            st.caption(status.get("message"))
        else:
            st.warning(status.get("message"))

    if get_structure_mode() != "primitive":
        st.caption("Auto-filled paths use the active cell. Switch to `Primitive Cell` first for textbook primitive-cell band paths.")


def render_qe_run_section(ctx: ProjectContext) -> None:
    """Render local QE execution controls and live output."""
    st.markdown("### Base `pw.x` Run")
    st.caption("Execute the generated base input locally with Quantum ESPRESSO.")

    input_path = ctx.input_files.ui_path
    if not input_path.is_file():
        st.warning("No runnable `pw.in` was found. Generate it from the Setup page before launching `pw.x`.")
        return

    run_active = bool(st.session_state.get(RUN_ACTIVE_STATE_KEY))
    active_owner = get_active_job_owner()
    summary = get_job_summary(JOB_OWNER_PW)
    output_path_text = summary.get("current_output_path") or str(QE_RUNS_DIR / QE_OUTPUT_FILENAME)

    render_job_status_block(JOB_OWNER_PW, idle_message="Launch `pw.x` against the generated `pw.in` input file.")
    st.caption(f"Command: `{' '.join(QE_RUN_COMMAND)}`")
    st.caption(f"Input file: `{input_path}`")
    st.caption(f"Full output log: `{output_path_text}`")

    run_clicked = st.button(
        "Run Calculation",
        use_container_width=True,
        disabled=run_active,
    )

    if run_clicked:
        if shutil.which(QE_RUN_COMMAND[0]) is None:
            st.error("Quantum ESPRESSO executable `pw.x` was not found on your PATH.")
        else:
            try:
                start_managed_qe_job(
                    owner=JOB_OWNER_PW,
                    job_label="Run Calculation",
                    steps=[
                        JobStep(
                            label="pw.x",
                            command=QE_RUN_COMMAND,
                            cwd=QE_RUNS_DIR,
                            output_path=QE_RUNS_DIR / QE_OUTPUT_FILENAME,
                        )
                    ],
                    log_paths={"pw.x log": str(QE_RUNS_DIR / QE_OUTPUT_FILENAME)},
                    artifacts={
                        "pw.in": str(input_path),
                        "pw.out": str(QE_RUNS_DIR / QE_OUTPUT_FILENAME),
                    },
                )
            except Exception as exc:
                reset_run_state(st.session_state)
                st.error(f"Failed to start Quantum ESPRESSO: {exc}")

    render_job_log_paths(JOB_OWNER_PW)
    render_job_output_panel(JOB_OWNER_PW, title="Base Run Console", empty_message="Run output will appear here.")

    if active_owner != JOB_OWNER_PW:
        render_job_artifact_summary(JOB_OWNER_PW)


def render_dos_runner_subsection() -> None:
    """Render DOS post-processing controls and status."""
    context = resolve_qe_run_context(QE_RUNS_DIR)
    prerequisites = validate_dos_prerequisites(context)
    default_dos_file = "dos.dat"

    st.markdown("#### Density of States (DOS)")
    st.caption("Run `dos.x` against saved QE data in `qe_runs/` without changing the main `pw.x` input form.")
    render_saved_run_context(context)
    render_prerequisite_feedback(prerequisites)

    with st.form("dos_runner_form"):
        row_one_col1, row_one_col2, row_one_col3 = st.columns(3)
        emin_ev = row_one_col1.number_input("Energy min (Emin, eV)", value=-10.0, step=0.5, format="%.3f")
        emax_ev = row_one_col2.number_input("Energy max (Emax, eV)", value=10.0, step=0.5, format="%.3f")
        deltae_ev = row_one_col3.number_input(
            "Energy step (DeltaE, eV)",
            min_value=0.001,
            value=0.1,
            step=0.01,
            format="%.3f",
        )

        row_two_col1, row_two_col2, row_two_col3 = st.columns(3)
        bz_sum = row_two_col1.selectbox("BZ sum method", options=DOS_BZ_SUM_OPTIONS, index=0)
        ngauss = row_two_col2.number_input("ngauss", value=0, step=1, disabled=bz_sum != "smearing")
        degauss_ry = row_two_col3.number_input(
            "degauss (Ry)",
            min_value=0.0,
            value=0.01,
            step=0.001,
            format="%.4f",
            disabled=bz_sum != "smearing",
        )

        fildos = st.text_input("Output filename", value=default_dos_file)
        run_clicked = st.form_submit_button(
            "Run DOS Workflow",
            use_container_width=True,
            disabled=bool(st.session_state.get(RUN_ACTIVE_STATE_KEY)),
        )

    dos_input_path = QE_RUNS_DIR / DOS_INPUT_FILENAME
    dos_log_path = QE_RUNS_DIR / DOS_OUTPUT_FILENAME
    try:
        dos_data_name = sanitize_leaf_filename(fildos, default_name=default_dos_file)
    except ValueError:
        dos_data_name = default_dos_file
    dos_data_path = QE_RUNS_DIR / dos_data_name

    render_job_status_block(JOB_OWNER_DOS, idle_message="Configure and launch `dos.x` from the saved QE run directory.")
    st.caption(f"Command: `{' '.join(DOS_COMMAND)}`")
    st.caption(f"Input file: `{dos_input_path}`")
    st.caption(f"Log file: `{dos_log_path}`")
    st.caption(f"DOS data file: `{dos_data_path}`")

    if run_clicked:
        if shutil.which(DOS_COMMAND[0]) is None:
            st.error("Quantum ESPRESSO executable `dos.x` was not found on your PATH.")
        elif not prerequisites.ok:
            st.error("DOS prerequisites are not satisfied.")
        elif emax_ev <= emin_ev:
            st.error("`Emax` must be greater than `Emin`.")
        else:
            try:
                dos_data_name = sanitize_leaf_filename(fildos, default_name=default_dos_file)
                dos_data_path = QE_RUNS_DIR / dos_data_name
                dos_input_text = build_dos_input_text(
                    prefix=context.prefix,
                    outdir=context.effective_outdir,
                    fildos=dos_data_name,
                    emin_ev=emin_ev,
                    emax_ev=emax_ev,
                    deltae_ev=deltae_ev,
                    bz_sum=bz_sum,
                    ngauss=int(ngauss) if bz_sum == "smearing" else None,
                    degauss_ry=degauss_ry if bz_sum == "smearing" else None,
                )
                dos_input_path.write_text(dos_input_text, encoding="utf-8")
                start_managed_qe_job(
                    owner=JOB_OWNER_DOS,
                    job_label="Density of States (DOS)",
                    steps=[
                        JobStep(
                            label="dos.x",
                            command=DOS_COMMAND,
                            cwd=QE_RUNS_DIR,
                            output_path=dos_log_path,
                            validator=lambda path=dos_data_path: validate_dos_output_file(path),
                        )
                    ],
                    log_paths={"dos.x log": str(dos_log_path)},
                    artifacts={
                        "dos.in": str(dos_input_path),
                        "dos.out": str(dos_log_path),
                        dos_data_name: str(dos_data_path),
                    },
                )
            except Exception as exc:
                reset_run_state(st.session_state)
                st.error(f"Failed to start DOS post-processing: {exc}")

    render_job_log_paths(JOB_OWNER_DOS)
    render_job_output_panel(JOB_OWNER_DOS, title="DOS Console", empty_message="DOS live output will appear here.")
    render_job_artifact_summary(JOB_OWNER_DOS)


def render_band_workflow_subsection(ctx: ProjectContext) -> None:
    """Render the dedicated band-structure workflow controls and status."""
    context = resolve_qe_run_context(QE_RUNS_DIR)
    default_nbnd = context.band_count_estimate.recommended_nbnd if context.band_count_estimate is not None else 16

    st.markdown("#### Band Structure")
    st.caption("Run a dedicated band-path `pw.x` calculation followed by `bands.x` using the saved SCF source in `qe_runs/`.")
    render_saved_run_context(context)
    st.info("This workflow is a new band-path run plus `bands.x` post-processing. It is not just post-processing of the original SCF job.")
    render_auto_band_path_controls(ctx)

    with st.form("bands_runner_form"):
        row_one_col1, row_one_col2 = st.columns(2)
        if BAND_NBND_WIDGET_KEY not in st.session_state:
            st.session_state[BAND_NBND_WIDGET_KEY] = int(default_nbnd)
        elif context.band_count_estimate is not None and st.session_state[BAND_NBND_WIDGET_KEY] < 1:
            st.session_state[BAND_NBND_WIDGET_KEY] = int(default_nbnd)
        if BAND_COORDINATE_MODE_WIDGET_KEY not in st.session_state:
            st.session_state[BAND_COORDINATE_MODE_WIDGET_KEY] = "crystal_b"
        if BAND_PATH_WIDGET_KEY not in st.session_state:
            st.session_state[BAND_PATH_WIDGET_KEY] = DEFAULT_BAND_PATH_TEXT

        nbnd = row_one_col1.number_input("nbnd", min_value=1, step=1, key=BAND_NBND_WIDGET_KEY)
        coordinate_mode = row_one_col2.selectbox(
            "Coordinate mode",
            options=BAND_COORDINATE_MODE_OPTIONS,
            key=BAND_COORDINATE_MODE_WIDGET_KEY,
        )
        st.caption("Paste rows only: `kx ky kz npoints ! label`")
        band_path_text = st.text_area(
            "Band-path points",
            height=180,
            help="Enter only the point rows. QE Studio writes the `K_POINTS` header and point count automatically.",
            key=BAND_PATH_WIDGET_KEY,
        )
        run_clicked = st.form_submit_button(
            "Run Band Structure Workflow",
            use_container_width=True,
            disabled=bool(st.session_state.get(RUN_ACTIVE_STATE_KEY)),
        )

    requested_nbnd = int(nbnd)
    effective_nbnd = get_effective_band_nbnd(context, requested_nbnd)
    prerequisites = validate_bands_prerequisites(
        context,
        nbnd=requested_nbnd,
        band_path_text=band_path_text,
    )
    render_prerequisite_feedback(prerequisites)
    if context.band_count_estimate is not None:
        estimate = context.band_count_estimate
        st.caption(
            "Saved-run estimate: "
            f"`nelec={estimate.electron_count:.3f}` from `{estimate.source}`, "
            f"minimum occupied bands `{estimate.minimum_nbnd}`, recommended `nbnd={estimate.recommended_nbnd}`."
        )
    if effective_nbnd != requested_nbnd:
        st.warning(
            f"Requested `nbnd={requested_nbnd}` is too small for the saved run. "
            f"QE Studio will write `nbnd={effective_nbnd}` into `bands_pw.in`."
        )
    if not context.has_restart_wavefunctions:
        st.caption(
            "No reusable restart wavefunction files were found in the save directory. "
            "The band-path run will reuse the saved potential and generate fresh wavefunctions."
        )

    bands_pw_input_path = QE_RUNS_DIR / BANDS_PW_INPUT_FILENAME
    bands_pw_log_path = QE_RUNS_DIR / BANDS_PW_OUTPUT_FILENAME
    bands_pp_input_path = QE_RUNS_DIR / BANDS_PP_INPUT_FILENAME
    bands_pp_log_path = QE_RUNS_DIR / BANDS_PP_OUTPUT_FILENAME
    filband_path = QE_RUNS_DIR / FILBAND_FILENAME
    filband_gnu_path = QE_RUNS_DIR / FILBAND_GNU_FILENAME

    render_job_status_block(JOB_OWNER_BANDS, idle_message="Configure a band path, then launch the dedicated `pw.x` + `bands.x` workflow.")
    st.caption(f"Step 1 command: `{' '.join(BANDS_PW_COMMAND)}`")
    st.caption(f"Step 1 input: `{bands_pw_input_path}`")
    st.caption(f"Step 1 log: `{bands_pw_log_path}`")
    st.caption(f"Step 2 command: `{' '.join(BANDS_PP_COMMAND)}`")
    st.caption(f"Step 2 input: `{bands_pp_input_path}`")
    st.caption(f"Step 2 log: `{bands_pp_log_path}`")
    st.caption(f"Band data: `{filband_path}` and `{filband_gnu_path}`")

    if run_clicked:
        missing_executables = [name for name in (BANDS_PW_COMMAND[0], BANDS_PP_COMMAND[0]) if shutil.which(name) is None]
        if missing_executables:
            st.error(
                "Required Quantum ESPRESSO executables were not found on your PATH: "
                + ", ".join(f"`{name}`" for name in missing_executables)
            )
        elif not prerequisites.ok:
            st.error("Band workflow prerequisites are not satisfied.")
        else:
            try:
                bands_pw_text = transform_input_to_bands_pw(
                    context,
                    nbnd=effective_nbnd,
                    coordinate_mode=coordinate_mode,
                    band_path_text=band_path_text,
                )
                bands_pw_input_path.write_text(bands_pw_text, encoding="utf-8")

                def prepare_bands_pp_input() -> None:
                    bands_pp_text = build_bands_pp_input_text(
                        prefix=context.prefix,
                        outdir=context.effective_outdir,
                        filband=FILBAND_FILENAME,
                    )
                    bands_pp_input_path.write_text(bands_pp_text, encoding="utf-8")

                start_managed_qe_job(
                    owner=JOB_OWNER_BANDS,
                    job_label="Band Structure Workflow",
                    steps=[
                        JobStep(
                            label="bands pw.x",
                            command=BANDS_PW_COMMAND,
                            cwd=QE_RUNS_DIR,
                            output_path=bands_pw_log_path,
                        ),
                        JobStep(
                            label="bands.x",
                            command=BANDS_PP_COMMAND,
                            cwd=QE_RUNS_DIR,
                            output_path=bands_pp_log_path,
                            prepare=prepare_bands_pp_input,
                            validator=lambda path=filband_gnu_path, band_input=bands_pw_input_path: validate_band_output_file(
                                path,
                                band_input_file=band_input,
                            ),
                        ),
                    ],
                    log_paths={
                        "bands pw.x log": str(bands_pw_log_path),
                        "bands.x log": str(bands_pp_log_path),
                    },
                    artifacts={
                        "bands_pw.in": str(bands_pw_input_path),
                        "bands_pw.out": str(bands_pw_log_path),
                        "bands_pp.in": str(bands_pp_input_path),
                        "bands_pp.out": str(bands_pp_log_path),
                        "filband": str(filband_path),
                        "filband.gnu": str(filband_gnu_path),
                    },
                )
            except Exception as exc:
                reset_run_state(st.session_state)
                st.error(f"Failed to start the band-structure workflow: {exc}")

    render_job_log_paths(JOB_OWNER_BANDS)
    render_job_output_panel(JOB_OWNER_BANDS, title="Band Workflow Console", empty_message="Band workflow live output will appear here.")
    render_job_artifact_summary(JOB_OWNER_BANDS)
