"""Exercise real page actions in two roots, with downloads/processes mocked."""
import io
import json
import os
from pathlib import Path
import tarfile
from unittest import mock

from ase.build import bulk
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from qe_studio.core import state_keys as keys, structure_state
from qe_studio.core.project_context import build_project_context
from qe_studio.core.project_paths import WorkspacePaths
from qe_studio.pages import ui_run, ui_setup
from qe_studio.services import pseudopotentials, qe_runner
from qe_studio.services.electronic_properties import find_available_analysis_files
from qe_studio.services.qe_control import parse_qe_control_from_input, resolve_qe_outdir
from qe_studio.convergence import (
    discover_cutoff_studies, discover_kpoint_studies, discover_eos_studies,
    refresh_cutoff_study, refresh_kpoint_study, refresh_eos_study,
)
from qe_studio.convergence.study_runner import (
    build_cutoff_study_steps, build_kpoint_study_steps, build_eos_study_steps,
)


def _inventory(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(params=["absolute", "relative"])
def workspaces(tmp_path, monkeypatch, request):
    unrelated = tmp_path / "unrelated"
    sentinel = unrelated / "qe_runs"
    sentinel.mkdir(parents=True)
    (sentinel / "pw.in").write_text("must not change")
    before = _inventory(sentinel)
    monkeypatch.chdir(unrelated)
    roots = [tmp_path / "workspace a", tmp_path / "workspace b"]
    if request.param == "relative":
        roots = [Path(os.path.relpath(root, unrelated)) for root in roots]
    for root in roots:
        (root / "pseudopotentials").mkdir(parents=True)
        (root / "pseudopotentials/Si.UPF").write_text(
            '<UPF><PP_HEADER element="Si" has_so="F" relativistic="scalar" /></UPF>'
        )
    st.session_state.clear()
    yield roots
    assert _inventory(sentinel) == before
    st.session_state.clear()
    qe_runner.RUN_REGISTRY.clear()


def _workspace_page(root, section, pseudo_root=None):
    # AppTest executes this function as a standalone Streamlit script.
    from pathlib import Path
    from ase.build import bulk
    import streamlit as st
    from qe_studio.core import state_keys as keys, structure_state
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages import ui_setup, ui_run, ui_studies

    root = Path(root)
    if "fixture_initialized" not in st.session_state:
        atoms = bulk("Si", "diamond", a=5.43)
        structure_state.commit_structure_source(atoms, source_kind="manual", source_label="Silicon")
        if section != "pseudos":
            directory = Path(pseudo_root or root) / "pseudopotentials"
            st.session_state[keys.PSEUDO_DIR_STATE_KEY] = str(directory)
            st.session_state[keys.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "Si.UPF"}
            st.session_state[keys.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [
                {"element": "Si", "local_path": str(directory / "Si.UPF")}
            ]
            st.session_state["pseudo_resolved_entries"] = {
                "Si": {"filename": "Si.UPF", "local_path": str(directory / "Si.UPF"),
                       "cutoff_wfc": 40.0, "cutoff_rho": 160.0}
            }
            st.session_state[keys.PSEUDO_RESOLUTION_STATUS_STATE_KEY] = {"state": "resolved"}
        st.session_state["fixture_initialized"] = True
    ctx = build_project_context(root)
    if section == "setup":
        ui_setup.render_qe_input_form(ctx)
    elif section == "pseudos":
        ui_setup.render_pseudopotentials_section(ctx)
    elif section == "studies":
        ui_studies.render_page(ctx)
    elif section == "base":
        ui_run.render_qe_run_section(ctx)
    elif section == "dos":
        ui_run.render_dos_runner_subsection(ctx)
    elif section == "bands":
        ui_run.render_band_workflow_subsection(ctx)


def _app(root, section, pseudo_root=None):
    app = AppTest.from_function(
        _workspace_page, args=(str(root), section, str(pseudo_root) if pseudo_root else None),
        default_timeout=20,
    ).run()
    assert not app.exception
    return app


def _click(app, label, *, expected_errors=()):
    button = next(button for button in app.button if button.label == label)
    assert not button.disabled
    button.click().run()
    assert not app.exception
    assert [error.value for error in app.error] == list(expected_errors)


def _generate(root):
    app = _app(root, "setup")
    _click(app, "Generate Input File")
    assert Path(app.session_state[keys.QE_INPUT_UI_PATH_STATE_KEY]) == root / "pw.in"
    assert app.session_state[keys.QE_INPUT_TEXT_STATE_KEY] == (root / "pw.in").read_text()
    return app


def test_setup_generation_and_provenance_are_isolated(workspaces):
    first, second = workspaces
    _generate(first)
    first_before = _inventory(first)
    _generate(second)
    assert _inventory(first) == first_before
    for root in workspaces:
        metadata = json.loads((root / "qe_input_metadata.json").read_text())
        assert Path(metadata["control"]["resolved_outdir"]) == root / "tmp"
        provenance = json.loads((root / "qe_studio_provenance.json").read_text())
        assert Path(provenance["records"]["pw_input"]["paths"]["ui_path"]) == root / "pw.in"
        text = (root / "pw.in").read_text()
        assert "pseudopotentials" in text
        assert str(second if root == first else first) not in text


@pytest.mark.parametrize("foreign_field", ["ui", "internal", "unowned"])
def test_foreign_preview_falls_back_to_current_workspace(workspaces, foreign_field):
    first, second = workspaces
    (second / "pw.in").write_text("workspace b input")
    st.session_state[keys.QE_INPUT_TEXT_STATE_KEY] = "workspace a preview"
    if foreign_field != "unowned":
        st.session_state[keys.QE_INPUT_UI_PATH_STATE_KEY] = str(
            (first if foreign_field == "ui" else second) / "pw.in"
        )
    if foreign_field == "internal":
        st.session_state[keys.QE_INPUT_INTERNAL_PATH_STATE_KEY] = str(first / "espresso.pwi")
    ctx = build_project_context(second)
    assert ctx.input_files.preview_source == "disk"
    assert ctx.input_files.ui_path == second / "pw.in"
    assert ctx.input_files.internal_path == second / "espresso.pwi"
    assert ui_setup.load_generated_input_preview_text(ctx) == "workspace b input"
    (second / "pw.in").unlink()
    assert not build_project_context(second).input_files.available


def test_foreign_pseudos_block_setup_and_studies(workspaces):
    first, second = workspaces
    before = [_inventory(root) for root in workspaces]
    app = _app(second, "setup", pseudo_root=first)
    button = next(button for button in app.button if button.label == "Generate Input File")
    assert button.disabled
    assert any("outside workspace" in item.value for item in app.warning)
    app = _app(second, "studies", pseudo_root=first)
    assert any("outside workspace" in item.value for item in app.warning)
    assert not app.button
    assert [_inventory(root) for root in workspaces] == before


@pytest.mark.parametrize("foreign_field", ["map", "entries", "rows", "symlink"])
def test_pseudo_guard_checks_all_resolution_paths(workspaces, foreign_field):
    first, second = workspaces
    st.session_state[keys.PSEUDO_DIR_STATE_KEY] = str(second / "pseudopotentials")
    foreign = str(first / "pseudopotentials/Si.UPF")
    if foreign_field == "map":
        st.session_state[keys.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": foreign}
    elif foreign_field == "entries":
        st.session_state["pseudo_resolved_entries"] = {"Si": {"local_path": foreign}}
    elif foreign_field == "rows":
        st.session_state[keys.PSEUDO_RESOLVED_ROWS_STATE_KEY] = [{"local_path": foreign}]
    else:
        link = second / "pseudopotentials/link.UPF"
        link.symlink_to(Path(foreign).resolve())
        st.session_state[keys.PSEUDO_RESOLVED_MAP_STATE_KEY] = {"Si": "link.UPF"}
    assert structure_state.get_workspace_pseudopotential_blocker(WorkspacePaths(second))


def _download(url, destination, **kwargs):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.name == "manifest.json":
        destination.write_text(json.dumps({"Si": {"filename": "Si.UPF", "cutoff_wfc": 40, "cutoff_rho": 160}}))
    elif destination.name == "archive.tar.gz":
        payload = b'<UPF><PP_HEADER has_so="F" relativistic="scalar" /></UPF>'
        with tarfile.open(destination, "w:gz") as archive:
            item = tarfile.TarInfo("Si.UPF")
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
    elif destination.name == "index.html":
        destination.write_text('<a href="Si_ONCV_PBE_FR-1.0.upf">Si</a>')
    else:
        destination.write_text('<UPF><PP_HEADER has_so="T" relativistic="full" /></UPF>')


@pytest.mark.parametrize("fully_relativistic", [False, True])
def test_resolve_and_clear_pseudo_caches_use_workspace_roots(workspaces, monkeypatch, fully_relativistic):
    monkeypatch.setattr(pseudopotentials, "download_file", _download)
    apps = []
    for root in workspaces:
        app = _app(root, "pseudos")
        if fully_relativistic:
            app.checkbox(key=keys.PSEUDO_FULLY_RELATIVISTIC_WIDGET_KEY).check().run()
        _click(app, "Resolve Pseudopotentials")
        directory = Path(app.session_state[keys.PSEUDO_DIR_STATE_KEY])
        paths = WorkspacePaths(root)
        expected = (paths.pseudo_base_dir / "sg15_oncv/PBE/fully_relativistic" if fully_relativistic
                    else paths.sssp_cache_dir / "1.3.0/PBE/efficiency")
        assert directory == expected / "extracted"
        assert (expected / "resolved_map.json").is_file()
        apps.append((app, expected))
    other_before = _inventory(workspaces[1])
    _click(apps[0][0], "Clear pseudopotential cache")
    assert not apps[0][1].exists()
    assert _inventory(workspaces[1]) == other_before


def _execute_steps(steps):
    calls = []

    class Process:
        def __init__(self, text):
            self.stdout = io.StringIO(text)

        def wait(self):
            return 0

    def launch(command, *, cwd, **kwargs):
        root = Path(cwd)
        calls.append((tuple(command), root))
        if command[0] == "dos.x":
            (root / "dos.dat").write_text("# Fermi energy = 0 eV\n-1 2 0\n0 3 1\n1 2 2\n")
        elif command[0] == "bands.x":
            (root / "filband.gnu").write_text("0 -1\n1 -0.5\n\n0 1\n1 2\n")
        return Process("!    total energy = -10.0 Ry\nJOB DONE.\n")

    with mock.patch.object(qe_runner.subprocess, "Popen", side_effect=launch):
        run_id, _ = qe_runner.run_chained_jobs(job_label="Isolation test", steps=steps)
        runtime = qe_runner.get_run_context(run_id)
        runtime.thread.join(timeout=10)
        assert not runtime.thread.is_alive()
        events = qe_runner.drain_run_events(run_id)
        assert events[-1].returncode == 0, events
        qe_runner.cleanup_run(run_id)
    return calls


@pytest.mark.parametrize("section,label", [
    ("base", "Run Calculation"), ("dos", "Run DOS Workflow"),
    ("bands", "Run Band Structure Workflow"),
])
def test_execution_pages_and_analysis_are_isolated(workspaces, monkeypatch, section, label):
    captured = []
    monkeypatch.setattr(ui_run, "start_managed_qe_job", lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(ui_run.shutil, "which", lambda name: f"/test/{name}")
    for index, root in enumerate(workspaces):
        _generate(root)
        control = parse_qe_control_from_input((root / "pw.in").read_text())
        (root / "tmp" / f"{control['prefix']}.save").mkdir(parents=True)
        other = workspaces[1 - index]
        before = _inventory(other)
        app = _app(root, section)
        _click(app, label)
        job = captured[-1]
        assert all(step.cwd == root and step.output_path.parent == root for step in job["steps"])
        assert all(Path(path).parent == root for path in job["log_paths"].values())
        assert all(Path(path).parent == root for path in job["artifacts"].values())
        calls = _execute_steps(job["steps"])
        assert all(cwd == root for _, cwd in calls)
        for step in job["steps"]:
            assert "JOB DONE." in step.output_path.read_text()
        files = find_available_analysis_files(root)
        if section == "dos":
            assert files.dos_file == root / "dos.dat"
        elif section == "bands":
            assert files.band_file == root / "filband.gnu"
            assert (root / "bands_pp.in").is_file()
        else:
            assert files.pw_output_file == root / "pw.out"
        assert _inventory(other) == before
        provenance = json.loads((root / "qe_studio_provenance.json").read_text())
        for record in provenance["records"].values():
            assert all(Path(path).parent == root for path in record["paths"].values())


@pytest.mark.parametrize("kind,label,discover,build_steps,refresh", [
    ("cutoff", "Generate Cutoff Study", discover_cutoff_studies, build_cutoff_study_steps, refresh_cutoff_study),
    ("kpoint", "Generate K-Point Convergence Study", discover_kpoint_studies, build_kpoint_study_steps, refresh_kpoint_study),
    ("eos", "Generate EOS Study", discover_eos_studies, build_eos_study_steps, refresh_eos_study),
])
def test_study_pages_generate_discover_and_run_in_supplied_root(workspaces, kind, label, discover, build_steps, refresh):
    for index, root in enumerate(workspaces):
        other = workspaces[1 - index]
        before = _inventory(other)
        app = _app(root, "studies")
        # The legacy EOS view reports an unavailable fit until points have run.
        expected_errors = (
            "Could not fit Equation of State to the provided points.",
            "Insufficient points for EOS fitting. Need at least 4, got 0.",
        ) if kind == "eos" else ()
        _click(app, label, expected_errors=expected_errors)
        studies = discover(WorkspacePaths(root).studies_dir)
        assert len(studies) == 1
        study = studies[0]
        # A rerun exercises the page's discovery/selector path, not only the service.
        app.run()
        assert not app.exception
        selected = app.session_state[f"{kind}_study_selected_path"]
        assert Path(selected) == study
        manifest = json.loads((study / f"{kind}_study_manifest.json").read_text())
        steps = build_steps(study, manifest)
        assert steps
        assert all(step.cwd.is_relative_to(root) for step in steps)
        assert all(str(other) not in (step.cwd / "pw.in").read_text() for step in steps)
        _execute_steps(steps)
        summary = refresh(study)
        assert all(point["status"] == "success" for point in summary["points"])
        assert _inventory(other) == before


def test_external_scratch_path_retains_legacy_semantics(workspaces, tmp_path):
    root = workspaces[0]
    external = tmp_path / "external-scratch"
    atoms = bulk("Si", "diamond", a=5.43)
    parameters = ui_setup.default_qe_parameters(atoms=atoms)
    parameters.update(outdir=str(external), outdir_source="user")
    result = ui_setup._generate_and_persist_qe_input(
        atoms, parameters, {"Si": "Si.UPF"}, str(root / "pseudopotentials"),
        run_dir=root, current_pseudo_settings=keys.DEFAULT_PSEUDO_SETTINGS,
    )
    assert result.success
    control = parse_qe_control_from_input(result.qe_input_text)
    assert resolve_qe_outdir(root, control["outdir"]) == external
    assert not external.exists()
