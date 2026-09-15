"""Project-owned pseudopotentials and baseline revision editor."""
import json

import streamlit as st

from ..core.project_session import widget_key
from ..services import baseline_service as baselines, pseudo_set_service as pseudos
from ..services.structure_service import load_structure
from ..services.input_generator import VdwMethod
from ..services.pseudopotentials import resolve_sssp_pseudopotentials, resolve_fully_relativistic_pseudopotentials


def render_page(ctx):
    project = ctx.project
    key = lambda name: widget_key(project.id, 'baseline:' + name)
    st.title('Baseline revisions')
    history = baselines.list_baselines(project)
    if history:
        lookup = {entry.id: entry for entry in history}
        selected = st.selectbox('View baseline', list(lookup), index=list(lookup).index(project.active_baseline_id) if project.active_baseline_id in lookup else len(history) - 1,
            format_func=lambda identifier, lookup=lookup: f'baseline_{lookup[identifier].sequence:03d}', key=key('history:' + str(project.active_baseline_id)))
        viewed = lookup[selected]
        st.caption(f'Structure: {viewed.structure_id} · Pseudo set: {viewed.pseudo_set_id} · Previous: {viewed.previous_baseline_id or "none"}')
        st.json(json.loads(viewed.settings_json))
        if project.active_baseline_id == selected:
            st.success('Active baseline')
        elif st.button('Set Active Baseline', key=key('adopt')):
            try:
                baselines.set_active_baseline(project, selected)
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))
    if not project.active_structure_id:
        st.info('Set an active structure before preparing a scientific configuration.')
        return
    try:
        atoms = load_structure(project, project.active_structure_id)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        return
    st.subheader('Pseudopotentials')
    st.caption('Resolve into the reusable cache, then commit a project-owned set. Baselines use committed sets only.')
    scope = project.active_structure_id
    family = st.selectbox('Download family', ['SSSP', 'Fully relativistic SG15'], key=key('family:' + scope))
    functional = st.selectbox('Functional', ['PBE', 'PBEsol'] if family == 'SSSP' else ['PBE'], key=key('functional:' + family + scope))
    mode = st.selectbox('SSSP mode', ['efficiency', 'precision'], key=key('mode:' + scope)) if family == 'SSSP' else 'fully_relativistic'
    resolution_key = key('resolution:' + scope)
    selection = (family, functional, mode)
    cached = st.session_state.get(resolution_key)
    if cached and cached['selection'] != selection:
        st.session_state.pop(resolution_key, None)
        cached = None
    if st.button('Resolve Pseudopotentials', key=key('resolve')):
        st.session_state.pop(resolution_key, None)
        try:
            with st.spinner('Resolving pseudopotentials…'):
                root = pseudos.download_cache_root()
                if family == 'SSSP':
                    result = resolve_sssp_pseudopotentials(atoms, version='1.3.0', functional=functional, mode=mode, use_cache=True, cache_root=root / 'sssp')
                else:
                    result = resolve_fully_relativistic_pseudopotentials(atoms, functional=functional, use_cache=True, cache_root=root)
            cached = {'selection': selection, 'result': result}
            st.session_state[resolution_key] = cached
        except (ValueError, OSError, RuntimeError) as exc:
            cached = None
            st.error(str(exc))
    if cached:
        result = cached['result']
        st.write('Resolved species: ' + ', '.join(sorted(result['resolved_entries'])))
        for warning in result.get('warnings', []):
            st.warning(warning)
        if st.button('Commit Pseudopotential Set', key=key('commit_pseudos')):
            try:
                committed = pseudos.commit_pseudo_set(project, result['resolved_entries'], settings=result['settings'])
                st.session_state[key('selected_pseudo:' + scope)] = committed.id
                st.session_state.pop(resolution_key, None)
                st.rerun()
            except (ValueError, OSError, RuntimeError) as exc:
                st.error(str(exc))

    sets = pseudos.list_pseudo_sets(project)
    if not sets:
        st.info('No project pseudopotential sets committed yet.')
        return
    lookup = {entry.id: entry for entry in sets}
    active = baselines.get_baseline(project, project.active_baseline_id) if project.active_baseline_id else None
    pseudo_key = key('selected_pseudo:' + scope)
    binding = (scope, project.active_baseline_id)
    if st.session_state.get(key('editor_binding')) != binding:
        st.session_state[key('editor_binding')] = binding
        if active:
            st.session_state[pseudo_key] = active.pseudo_set_id
    if st.session_state.get(pseudo_key) not in lookup:
        st.session_state[pseudo_key] = active.pseudo_set_id if active else sets[-1].id
    pseudo_id = st.selectbox('Committed pseudopotential set', list(lookup),
        format_func=lambda identifier: f'set_{lookup[identifier].sequence:03d} · {lookup[identifier].pseudo_set_hash[:12]}', key=pseudo_key)
    try:
        entries = pseudos.load_pseudo_entries(project, pseudo_id)
        if set(entries) != set(atoms.get_chemical_symbols()):
            st.warning('This set does not match the active structure species. Select another set or resolve a new one.')
            return
        st.caption('Verified project-owned UPFs: ' + ', '.join(entries))
        defaults = json.loads(active.settings_json) if active else baselines.default_baseline_settings(atoms, entries)
        editor_scope = scope + ':' + pseudo_id + ':' + str(project.active_baseline_id)
        st.subheader('Common physical and numerical defaults')
        st.caption('Edit the baseline settings below. Committing creates a new revision; Set Active Baseline adopts it.')
        field = lambda name: key(editor_scope + ':' + name)
        with st.form(key('editor:' + editor_scope)):
            columns = st.columns(2)
            ecutwfc = columns[0].number_input('Wavefunction cutoff (Ry)', value=float(defaults['ecutwfc']), min_value=0.1, key=field('ecutwfc'))
            ecutrho = columns[1].number_input('Charge density cutoff (Ry)', value=float(defaults['ecutrho']), min_value=0.1, key=field('ecutrho'))
            columns = st.columns(3)
            grid = [column.number_input('k-grid ' + axis, min_value=1, value=int(defaults['k_points_grid'][i]), step=1, key=field('k' + axis)) for i, (column, axis) in enumerate(zip(columns, 'abc'))]
            columns = st.columns(2)
            conv_thr = columns[0].number_input('SCF convergence threshold (Ry)', value=float(defaults['conv_thr']), min_value=1e-14, format='%.2e', key=field('conv_thr'))
            mixing = columns[1].number_input('Mixing beta', value=float(defaults['mixing_beta']), min_value=0.01, max_value=1.0, key=field('mixing'))
            nspin = st.selectbox('Spin', [1, 2], index=[1, 2].index(defaults['nspin']), format_func=lambda value: 'Non-magnetic' if value == 1 else 'Collinear spin', key=field('spin'))
            soc = st.checkbox('Spin–orbit coupling', value=bool(defaults['lspinorb'] or defaults['noncolin']), key=field('soc'))
            magnetization = st.text_area('Starting magnetization by species (JSON)', value=json.dumps(defaults['starting_magnetization'], indent=2), key=field('magnetization'))
            occupations_options = ['fixed', 'smearing', 'tetrahedra', 'tetrahedra_lin', 'tetrahedra_opt']
            occupations = st.selectbox('Occupations', occupations_options, index=occupations_options.index(defaults['occupations']), key=field('occupations'))
            smearing = st.text_input('Smearing method', value=defaults.get('smearing') or 'gaussian', key=field('smearing'))
            degauss = st.number_input('Smearing width (Ry)', value=float(defaults.get('degauss') or 0.01), min_value=0.0, key=field('degauss'))
            use_u = st.checkbox('Hubbard U', value=bool(defaults['dftu_enabled']), key=field('use_u'))
            hubbard = st.text_area('Hubbard parameters by species (JSON, u_ev in eV)', value=json.dumps(defaults['hubbard_u'], indent=2), key=field('hubbard'))
            vdw_options = [method.value for method in VdwMethod]
            vdw = st.selectbox('van der Waals method', vdw_options, index=vdw_options.index(defaults['vdw_method']), key=field('vdw'))
            validate = st.form_submit_button('Preview Baseline')
            submitted = st.form_submit_button('Commit Baseline Revision')
        if validate or submitted:
            settings = {**defaults, 'ecutwfc': ecutwfc, 'ecutrho': ecutrho, 'k_points_grid': grid,
                        'conv_thr': conv_thr, 'mixing_beta': mixing, 'nspin': nspin,
                        'noncolin': soc, 'lspinorb': soc, 'starting_magnetization': json.loads(magnetization),
                        'occupations': occupations, 'smearing': smearing, 'degauss': degauss,
                        'dftu_enabled': use_u, 'hubbard_u': json.loads(hubbard), 'vdw_method': vdw}
            if submitted:
                entry = baselines.commit_baseline(project, scope, pseudo_id, settings, previous_baseline_id=project.active_baseline_id)
                st.session_state[key('committed_notice')] = f'Created baseline_{entry.sequence:03d}. Select it in history and Set Active Baseline to adopt it.'
                st.rerun()
            else:
                request, rendered, _ = baselines.resolve_baseline_settings(project, scope, pseudo_id, settings)
                st.success('Valid baseline preview. No revision created.')
                st.json(request)
                for issue in rendered['validation']:
                    st.warning(issue['message'])
        notice = st.session_state.pop(key('committed_notice'), None)
        if notice:
            st.success(notice)
    except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
        st.error(str(exc))
