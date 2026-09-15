"""Persistent calculation browser/editor with attempt-specific execution views."""
import json
from collections import deque

import streamlit as st

from ..core.project_session import widget_key
from ..core.project_paths import ProjectPaths
from ..services import calculation_service as calculations, execution_service as execution, execution_coordinator as coordinator
from ..services.baseline_service import list_baselines
from ..services.electronic_properties import AnalysisFiles, parse_qe_dos, parse_qe_band_structure, make_dos_figure, make_band_figure
from .freshness_view import render_assessment
from ..services import dependency_service


def _parents(project, kind, baseline_id):
    expected = {'nscf': 'scf', 'bands': 'scf', 'dos': 'nscf'}.get(kind)
    if not expected:
        return {}
    result = {}
    for run in execution.list_runs(project):
        calculation = calculations.get_calculation(project, run['calculation_id'])
        if run['state'] == 'completed' and calculation.kind == expected and calculation.baseline_id == baseline_id:
            result[run['id']] = f"{calculation.label} / Run #{run['attempt']:03d}"
    return result


def _duplicate(project, identifier, selection_key):
    copied = calculations.duplicate_calculation(project, identifier)
    st.session_state[selection_key] = copied.id


def _duplicate_current(project, identifier, selection_key):
    try:
        copied = dependency_service.duplicate_using_baseline(project, identifier)
        st.session_state[selection_key] = copied.id
    except (ValueError, OSError) as exc:
        st.error(str(exc))


def render_page(ctx):
    project = ctx.project
    key = lambda name: widget_key(project.id, 'calculations:' + name)
    pending = st.session_state.pop(key('pending_selection'), None)
    if pending:
        st.session_state[key('selected')] = pending
    st.title('Calculations')
    coordinator.reconcile_runs(project)
    baselines = list_baselines(project)
    if not baselines:
        st.info('Commit a baseline before creating a calculation.')
        return
    baseline_labels = {entry.id: f'baseline_{entry.sequence:03d}' for entry in baselines}
    with st.expander('New Calculation'):
        kind = st.selectbox('Calculation kind', calculations.KINDS, key=key('new_kind'))
        baseline_id = st.selectbox('Starting baseline', list(baseline_labels),
            index=list(baseline_labels).index(project.active_baseline_id) if project.active_baseline_id in baseline_labels else len(baselines)-1,
            format_func=baseline_labels.get, key=key('new_baseline'))
        parents = _parents(project, kind, baseline_id)
        parent_id = None
        if kind in {'nscf','bands','dos'}:
            parent_id = st.selectbox('Parent run', [None, *parents], format_func=lambda value: parents.get(value, 'Select later'), key=key('new_parent:' + kind + baseline_id))
        if st.button('Create Calculation', key=key('create')):
            try:
                calculation = calculations.create_calculation(project, kind, baseline_id, parent_run_id=parent_id,
                    origin='workflow_generated' if parent_id else 'manual')
                st.session_state[key('selected')] = calculation.id
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
    items = calculations.list_calculations(project)
    if not items:
        st.info('No calculations yet.')
        return
    category = st.selectbox('Filter by kind', ['All', *calculations.KINDS], key=key('filter'))
    show_study = st.checkbox('Show study calculations', key=key('show_study'))
    visible = [item for item in items if (category == 'All' or item.kind == category) and (show_study or item.origin != 'study_point')]
    if not visible:
        st.info('No calculations of this kind.')
        return
    latest_runs = {run['calculation_id']: run['state'] for run in execution.list_runs(project)}
    labels = {item.id: f'{item.label} · {item.display_name} · {latest_runs.get(item.id, item.state).replace("_", " ")}' for item in visible}
    if st.session_state.get(key('selected')) not in labels:
        st.session_state[key('selected')] = visible[-1].id
    identifier = st.selectbox('Selected calculation', list(labels), format_func=labels.get, key=key('selected'))
    calculation = calculations.get_calculation(project, identifier)
    study_owner = calculations.study_point_owner(project, identifier)
    definition_locked = bool(calculation.frozen_at or study_owner)
    scope = lambda name: key(identifier + ':' + name)
    st.caption(f'{calculation.label} · Draft revision {calculation.draft_revision} · Generated revision {calculation.generated_revision or "none"}')
    input_status = st.empty()
    frozen_status = st.empty()
    if calculation.state == 'input_outdated':
        input_status.warning('Input needs regeneration.')
    if calculation.frozen_at:
        frozen_status.info('Scientific definition frozen by its first run. Duplicate to edit; names and notes remain editable.')
    elif study_owner:
        frozen_status.info('This point belongs to an immutable study specification. Duplicate the study to change it.')
    overview, parameters, inputs, runs, results = st.tabs(['Overview', 'Parameters', 'Input', 'Runs', 'Results'])
    with overview:
        render_assessment(project, 'calculation', identifier)
        st.write('Baseline', baseline_labels[calculation.baseline_id])
        st.write('Origin', calculation.origin)
        st.write('Definition hash', calculation.definition_hash or 'Not generated')
        if calculation.parent_run_id:
            st.write('Parent run', calculation.parent_run_id)
        with st.form(scope('rename')):
            name = st.text_input('Display name', value=calculation.display_name, key=scope('name'))
            notes = st.text_area('Notes', value=calculation.notes, key=scope('notes'))
            if st.form_submit_button('Save Name and Notes'):
                calculations.rename_calculation(project, identifier, name, notes=notes)
                st.rerun()
        st.button('Duplicate Calculation', key=scope('duplicate'), on_click=_duplicate,
                  args=(project, identifier, key('pending_selection')))
        st.button('Duplicate using current baseline', key=scope('duplicate_current'), on_click=_duplicate_current,
                  args=(project, identifier, key('pending_selection')), disabled=not project.active_baseline_id)
    with parameters:
        snapshot_key = scope('editor_snapshot')
        snapshot = st.session_state.setdefault(snapshot_key, calculation)
        if snapshot.draft_revision != calculation.draft_revision:
            st.warning('A newer saved revision is available. Reload the editor before saving.')
        revision_key = scope('draft:' + str(snapshot.draft_revision))
        st.caption('Baseline defaults are inherited. Save overrides before generating input; widget values are an editing buffer.')
        st.json(json.loads(snapshot.resolved_request_json))
        with st.form(scope('parameters')):
            selected_baseline = st.selectbox('Baseline revision', list(baseline_labels), index=list(baseline_labels).index(snapshot.baseline_id),
                format_func=baseline_labels.get, key=revision_key + ':baseline', disabled=definition_locked)
            overrides = st.text_area('Calculation overrides (JSON)', value=json.dumps(json.loads(snapshot.overrides_json), indent=2),
                height=230, key=revision_key + ':overrides', disabled=definition_locked)
            st.caption('Examples: ecutwfc, ecutrho, k_points_grid; nbnd and band_path for bands. Leave a field absent to inherit it.')
            parent_options = _parents(project, calculation.kind, selected_baseline)
            parent = snapshot.parent_run_id
            if calculation.kind in {'nscf','bands','dos'}:
                options = [None, *parent_options]
                parent = st.selectbox('Completed parent run', options, index=options.index(parent) if parent in options else 0,
                    format_func=lambda value: parent_options.get(value, 'None'), key=revision_key + ':parent', disabled=bool(calculation.frozen_at))
            submitted = st.form_submit_button('Save Draft', disabled=definition_locked)
        if submitted:
            try:
                updated = calculations.save_draft(project, identifier, json.loads(overrides), expected_revision=snapshot.draft_revision,
                    baseline_id=selected_baseline, parent_run_id=parent)
                st.session_state[snapshot_key] = updated
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
        st.button('Reload Editor', key=scope('reload'), on_click=st.session_state.pop, args=(snapshot_key, None))
    with inputs:
        if st.button('Generate Input', key=scope('generate'), disabled=bool(calculation.frozen_at)):
            try:
                calculations.generate_calculation(project, identifier, expected_revision=calculation.draft_revision)
                st.rerun()
            except (ValueError, OSError, RuntimeError, TypeError) as exc:
                st.error(str(exc))
        if calculation.input_relpath:
            try:
                for filename, path in calculations.generated_inputs(project, calculation).items():
                    if filename.endswith('.in'):
                        st.caption(f'{filename} · generated revision {calculation.generated_revision}')
                        st.code(path.read_text(), language='fortran')
                        st.download_button('Download ' + filename, path.read_bytes(), file_name=filename, key=scope('download:' + filename))
            except (ValueError, OSError) as exc:
                st.error(str(exc))
    with runs:
        render_runs(project, identifier)
    with results:
        render_results(project, identifier)


@st.fragment(run_every=2.0)
def render_runs(project, calculation_id):
    key = lambda name: widget_key(project.id, 'runs:' + calculation_id + ':' + name)
    calculation = calculations.get_calculation(project, calculation_id)
    runs = execution.list_runs(project, calculation_id)
    if st.button('Run Calculation' if not runs else 'Create New Attempt', key=key('launch'), disabled=calculation.state != 'input_generated'):
        try:
            coordinator.submit_calculation(project, calculation_id, expected_revision=calculation.draft_revision)
            st.rerun()
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    if not runs:
        st.caption('No attempts. The first attempt freezes the scientific definition.')
        return
    labels = {run['id']: f"Run #{run['attempt']:03d} · {run['state']}" for run in runs}
    selected = st.selectbox('Attempt', list(labels), index=len(runs)-1, format_func=labels.get, key=key('selected'))
    run = execution.get_run(project, selected)
    st.write('Status', run['state'])
    if run['failure_message']:
        st.warning(run['failure_message'])
    if run['state'] == 'queued' and st.button('Start Queued Attempt', key=key('start')):
        try:
            coordinator.submit_run(project, selected)
            st.rerun()
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    if run['state'] in {'queued','running','interrupted'} and st.button('Cancel Attempt', key=key('cancel')):
        coordinator.cancel_run(project, selected)
        st.rerun()
    paths = ProjectPaths(project.root_path)
    for step in execution.run_steps(project, selected):
        st.caption(f"Step {step['ordinal']}: {step['kind']} · {step['state']} · exit {step['exit_code']}")
        path = paths.resolve_relative(step['output_relpath'])
        if path.is_file():
            with path.open(errors='replace') as handle:
                st.code(''.join(deque(handle, maxlen=200)))
    st.button('Refresh Attempts', key=key('refresh'))


def render_results(project, calculation_id):
    runs = execution.list_runs(project, calculation_id)
    if not runs:
        st.caption('Results will be linked to a specific attempt.')
        return
    key = lambda name: widget_key(project.id, 'results:' + calculation_id + ':' + name)
    labels = {run['id']: f"Run #{run['attempt']:03d} · {run['state']}" for run in runs}
    selected = st.selectbox('Result attempt', list(labels), index=len(runs)-1, format_func=labels.get, key=key('selected'))
    run = execution.get_run(project, selected)
    st.json(json.loads(run['scientific_json']))
    render_assessment(project, 'run', selected)
    paths = ProjectPaths(project.root_path)
    root = paths.resolve_relative(run['work_relpath'])
    files = {}
    try:
        for artifact in execution.artifacts(project, selected):
            path = paths.resolve_relative(artifact['relpath'])
            if artifact['role'] in {'result', 'output', 'input', 'restart'}:
                if not path.exists():
                    continue  # Missing restart data must not hide an intact historical plot.
                if execution.content_hash(path) != artifact['sha256']:
                    continue  # The assessment above exposes the integrity problem.
                files[path.name] = path
        request = json.loads(json.loads(run['frozen_definition_json'])['resolved_request_json'])
        restart = files.get(request['prefix'] + '.save')
        xml = restart / 'data-file-schema.xml' if restart else None
        analysis = AnalysisFiles(root, qe_input_file=files.get('pw.in'), dos_file=files.get('dos.dat'),
            band_file=files.get('filband.gnu'), band_input_file=files.get('pw.in'),
            xml_metadata_file=xml if xml and xml.is_file() else None, pw_output_file=files.get('pw.out'))
        if analysis.dos_file:
            st.plotly_chart(make_dos_figure(parse_qe_dos(analysis.dos_file, analysis)), key=key('dos'))
        if analysis.band_file:
            st.plotly_chart(make_band_figure(parse_qe_band_structure(analysis.band_file, analysis_files=analysis)), key=key('bands'))
    except (ValueError, OSError, RuntimeError) as exc:
        st.error(str(exc))
