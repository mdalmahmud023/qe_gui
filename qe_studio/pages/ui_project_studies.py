"""Persistent studies with point calculations and historical result selection."""
import json

import streamlit as st

from ..core.project_session import widget_key
from ..services.studies import study_service as studies
from ..services import execution_coordinator as coordinator
from ..services.baseline_service import list_baselines
from ..services.plotting.study_plots import plot_cutoff_convergence, plot_kpoint_convergence, plot_eos_study
from .freshness_view import render_assessment
from ..services import dependency_service


def _point_table(rows):
    displayed = []
    for row in rows:
        if 'ecutwfc_ry' in row:
            value = f"{row['ecutwfc_ry']:g} Ry / {row['ecutrho_ry']:g} Ry"
        elif 'grid_label' in row:
            value = row['grid_label']
        else:
            value = f"Scale {row['scale_factor']:.10g} · {row['volume']:.8g} Å³"
        displayed.append({'Point': row['ordinal'], 'Value': value, 'Calculation': row['calculation'],
            'Run': f"#{row['attempt']:03d}" if row.get('attempt') else '—',
            'Status': row.get('status', row['execution_state']).replace('_', ' '),
            'Energy (Ry)': row.get('total_energy_ry')})
    return displayed


def _open_calculation(project, identifier, page):
    prefix = lambda name: widget_key(project.id, 'calculations:' + name)
    st.session_state[prefix('pending_selection')] = identifier
    st.session_state[prefix('show_study')] = True
    st.session_state[prefix('filter')] = 'All'
    if page is not None:
        st.switch_page(page)


def render_page(ctx, calculation_page=None):
    project = ctx.project
    key = lambda name: widget_key(project.id, 'studies:' + name)
    pending = st.session_state.pop(key('pending_selection'), None)
    if pending:
        st.session_state[key('selected')] = pending
    coordinator.reconcile_runs(project)
    st.title('Studies')
    baselines = list_baselines(project)
    labels = {b.id: f'baseline_{b.sequence:03d}' for b in baselines}
    with st.expander('New Study'):
        if baselines:
            kind = st.selectbox('Study kind', studies.KINDS, key=key('new_kind'))
            baseline = st.selectbox('Study baseline', list(labels), format_func=labels.get,
                index=list(labels).index(project.active_baseline_id) if project.active_baseline_id in labels else len(labels)-1,
                key=key('new_baseline'))
            specification = st.text_area('New sweep specification (JSON)', json.dumps(studies.DEFAULT_SPECS[kind], indent=2), key=key('new_spec:' + kind))
            if st.button('Create Study', key=key('create')):
                try:
                    study = studies.create_study(project, kind, baseline, json.loads(specification))
                    st.session_state[key('selected')] = study['id']
                    st.rerun()
                except (ValueError, OSError, TypeError) as exc:
                    st.error(str(exc))
        else:
            st.info('Commit a baseline before creating a study.')
    items = studies.list_studies(project)
    if not items:
        st.info('No saved studies yet.')
        return
    display = {}
    for item in items:
        points = studies.point_statuses(project, item['id'])
        completed = sum(p['execution_state'] == 'completed' for p in points)
        progress = f'{completed} / {len(points)} complete' if item['state'] == 'generated' else item['state']
        display[item['id']] = f"{item['kind']}_{item['sequence']:03d} · {item['display_name']} · {progress}"
    if st.session_state.get(key('selected')) not in display:
        st.session_state[key('selected')] = items[-1]['id']
    identifier = st.selectbox('Selected study', list(display), format_func=display.get, key=key('selected'))
    study = studies.get_study(project, identifier)
    scope = lambda name: key(identifier + ':' + name)
    overview, points_tab, results_tab, plot_tab = st.tabs(['Overview', 'Points', 'Results', 'Plot'])
    with overview:
        render_assessment(project, 'study', identifier)
        st.write('Baseline', labels.get(study['baseline_id'], study['baseline_id']))
        st.caption(f"Definition: {study['state']} · revision {study['draft_revision']}")
        st.caption('Generating points freezes this experiment. Duplicate it to change the sweep.')
        saved_spec = json.loads(study['spec_json'])
        snapshot = st.session_state.setdefault(scope('editor'), study)
        with st.form(scope('draft')):
            edited = st.text_area('Sweep specification (JSON)', json.dumps(json.loads(snapshot['spec_json'])['request'], indent=2),
                key=scope('spec:' + str(snapshot['draft_revision'])), disabled=study['state'] != 'draft')
            saved = st.form_submit_button('Save Study Draft', disabled=study['state'] != 'draft')
        if saved:
            try:
                st.session_state[scope('editor')] = studies.save_draft(project, identifier, json.loads(edited), expected_revision=snapshot['draft_revision'])
                st.rerun()
            except (ValueError, OSError, TypeError) as exc:
                st.error(str(exc))
        st.button('Reload Study Editor', key=scope('reload'), on_click=st.session_state.pop, args=(scope('editor'), None))
        st.caption(f"{len(saved_spec['points'])} distinct resolved points")
        if st.button('Resume Generation' if study['state'] == 'generating' else 'Generate Study Points',
                     key=scope('generate'), disabled=study['state'] == 'generated'):
            try:
                studies.generate_study(project, identifier, expected_revision=study['draft_revision'])
                st.rerun()
            except (ValueError, OSError, RuntimeError, TypeError) as exc:
                st.error(str(exc))
        if st.button('Duplicate Study', key=scope('duplicate')):
            duplicate = studies.duplicate_study(project, identifier)
            st.session_state[key('pending_selection')] = duplicate['id']
            st.rerun()
    with points_tab:
        render_points(project, identifier, calculation_page)
    with results_tab:
        if st.button('Refresh Study Results', key=scope('refresh'), disabled=study['state'] != 'generated'):
            try:
                result = studies.refresh_results(project, identifier)
                st.session_state[scope('result')] = result['id']
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
        results = studies.list_results(project, identifier)
        summary = None
        if results:
            result_labels = {r['id']: f"Result #{r['revision']:03d} · {r['created_at']}" for r in results}
            selected = st.selectbox('Result revision', list(result_labels), index=len(results)-1,
                                    format_func=result_labels.get, key=scope('result'))
            summary = json.loads(studies.get_result(project, selected)['summary_json'])
            assessment = render_assessment(project, 'study_result', selected)
            st.write('Conclusion', summary['status'])
            st.json(summary.get('recommendation', summary.get('fit', {})))
            st.dataframe(_point_table(summary['points']), hide_index=True)
            st.caption('This revision records exact point attempts, output hashes, and parser/algorithm versions.')
            st.download_button('Download Result JSON', json.dumps(summary, indent=2), file_name=f"result_{summary['revision']:03d}.json", key=scope('download'))
            if st.button('Apply Recommendation to New Baseline', key=scope('apply'),
                disabled=summary.get('status') != 'complete' or assessment.state != dependency_service.Freshness.CURRENT
                         or any(f.integrity == 'corrupt' for f in assessment.files)):
                try:
                    baseline = dependency_service.apply_recommendation(project, selected)
                    st.success(f'Created baseline_{baseline.sequence:03d} with recommendation provenance. Use Baseline → Set Active Baseline to adopt it.')
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
            if study['kind'] == 'eos' and st.button('Create Equilibrium Structure', disabled=not summary.get('fit', {}).get('success'), key=scope('derive')):
                try:
                    structure = studies.derive_equilibrium_structure(project, selected)
                    st.success(f'Created structure_{structure.sequence:03d}. Use Structure → Set Active to adopt it.')
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
        else:
            st.info('Refresh results to record the first conclusion, including missing or failed points.')
    with plot_tab:
        if summary:
            mode = st.selectbox('Figure mode', ['interactive', 'publication'], key=scope('figure_mode'))
            if study['kind'] == 'eos':
                basis = st.selectbox('EOS display basis', ['cell_total', 'per_atom', 'relative_per_atom'], key=scope('basis'))
                figure = plot_eos_study(summary, display_mode=basis, figure_mode=mode)
            else:
                plot = plot_cutoff_convergence if study['kind'] == 'cutoff' else plot_kpoint_convergence
                figure = plot(summary, figure_mode=mode)
            st.plotly_chart(figure, key=scope('plot'))
        else:
            st.info('Select a saved result revision to plot.')


@st.fragment(run_every=2.0)
def render_points(project, identifier, calculation_page=None):
    key = lambda name: widget_key(project.id, 'study_points:' + identifier + ':' + name)
    coordinator.reconcile_runs(project)
    rows = studies.point_statuses(project, identifier)
    if not rows:
        st.caption('Generate the study to create its SCF point calculations.')
        return
    st.dataframe(_point_table(rows), hide_index=True)
    active = any(r['execution_state'] in {'queued','running'} for r in rows)
    if st.button('Run Pending / Failed Points', key=key('run'), disabled=any(r['execution_state'] == 'running' for r in rows)):
        try:
            studies.run_study(project, identifier)
            st.rerun()
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    if st.button('Stop Study', key=key('stop'), disabled=not active):
        coordinator.cancel_batch(project, identifier)
        for row in rows:
            if row['run_id'] and row['execution_state'] in {'queued','running'}:
                coordinator.cancel_run(project, row['run_id'])
        st.rerun()
    batch = coordinator.BATCHES.get((project.id, identifier))
    if batch and batch.get('error'):
        st.error(batch['error'])
    labels = {r['calculation_id']: f"Point {r['ordinal']} → {r['calculation']}" for r in rows}
    calculation = st.selectbox('Point calculation', list(labels), format_func=labels.get, key=key('calculation'))
    st.button('Open Calculation', key=key('open'), on_click=_open_calculation, args=(project, calculation, calculation_page))
