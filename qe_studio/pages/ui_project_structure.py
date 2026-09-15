"""Project structure previews and immutable history, separate from legacy state."""
import json
from uuid import uuid4

import streamlit as st
import streamlit.components.v1 as components

from ..core.project_session import widget_key
from ..services import structure_service as structures
from ..services.structure_sources import (import_structure_file, build_manual_structure,
    build_rocksalt, build_cubic_perovskite, build_ordered_double_perovskite)
from ..services.structure_viewer import build_structure_viewer_html, ViewerSettings


def _view(project, atoms, scope):
    st.caption(f'{atoms.get_chemical_formula()} · {len(atoms)} atoms · Volume {atoms.get_volume():.8g} Å³')
    style = st.selectbox('Viewer style', ['ball_and_stick', 'stick', 'spacefill'], key=widget_key(project.id, scope + ':style'))
    components.html(build_structure_viewer_html(atoms, settings=ViewerSettings(style_mode=style), height=380,
                    orientation_key=widget_key(project.id, scope + ':orientation')), height=390)


def _save_preview(project, result, *, original_bytes=None, original_filename=None):
    st.session_state[widget_key(project.id, 'source_preview')] = {
        'result': result, 'original_bytes': original_bytes, 'original_filename': original_filename, 'token': str(uuid4())}


def render_page(ctx):
    project = ctx.project
    key = lambda name: widget_key(project.id, name)
    st.title('Structure history')
    st.caption('Preview → Apply creates a version. Set Active selects the structure used for a new baseline.')
    preview_key = key('source_preview')
    source = st.radio('New structure source', ['Import file', 'Manual builder', 'Prototype'], horizontal=True, key=key('source_kind'))
    # A preview belongs to one source mode; changing modes must not commit an old draft.
    if st.session_state.get(key('preview_source_mode')) != source:
        st.session_state.pop(preview_key, None)
        st.session_state[key('preview_source_mode')] = source
    try:
        if source == 'Import file':
            uploaded = st.file_uploader('Structure file (CIF, VASP, XSF, XYZ)', key=key('upload'))
            if st.button('Preview Import', key=key('preview_import'), disabled=uploaded is None):
                st.session_state.pop(preview_key, None)
                payload = uploaded.getvalue()
                result = import_structure_file(filename=uploaded.name, file_bytes=payload)
                _save_preview(project, result, original_bytes=payload, original_filename=uploaded.name)
        elif source == 'Manual builder':
            with st.form(key('manual')):
                columns = st.columns(3)
                lengths = [column.number_input(axis + ' (Å)', value=5.0, min_value=0.01, key=key('cell_' + axis)) for column, axis in zip(columns, 'abc')]
                columns = st.columns(3)
                angles = [column.number_input(angle + ' (degrees)', value=90.0, min_value=0.01, max_value=179.99, key=key(angle)) for column, angle in zip(columns, ['alpha', 'beta', 'gamma'])]
                mode = st.selectbox('Coordinates', ['fractional', 'cartesian'], key=key('coordinates'))
                rows = st.text_area('Sites: symbol x y z, one per line', value='Si 0 0 0\nSi 0.25 0.25 0.25', key=key('sites'))
                preview = st.form_submit_button('Preview Build')
            if preview:
                st.session_state.pop(preview_key, None)
                atom_rows = []
                for line in rows.splitlines():
                    if line.strip():
                        symbol, x, y, z = line.split()
                        atom_rows.append(dict(symbol=symbol, x=float(x), y=float(y), z=float(z)))
                _save_preview(project, build_manual_structure(a=lengths[0], b=lengths[1], c=lengths[2],
                    alpha=angles[0], beta=angles[1], gamma=angles[2], coordinate_mode=mode, atom_rows=atom_rows))
        else:
            prototype = st.selectbox('Prototype', ['Rocksalt', 'Perovskite', 'Double perovskite'], key=key('prototype'))
            with st.form(key('prototype_form')):
                a_site = st.text_input('A species', value='Na', key=key('a_site'))
                b_site = st.text_input('B species', value='F' if prototype == 'Rocksalt' else 'Mg', key=key('b_site:' + prototype))
                x_site = st.text_input('X species', value='F', key=key('x_site')) if prototype != 'Rocksalt' else None
                b_prime = st.text_input("B′ species", value='Cd', key=key('b_prime')) if prototype == 'Double perovskite' else None
                lattice = st.number_input('Lattice parameter (Å)', min_value=0.01, value=5.64, key=key('lattice'))
                preview = st.form_submit_button('Preview Prototype')
            if preview:
                st.session_state.pop(preview_key, None)
                arguments = dict(a_site=a_site, b_site=b_site, lattice_parameter=lattice)
                if prototype == 'Rocksalt':
                    result = build_rocksalt(**arguments)
                elif prototype == 'Perovskite':
                    result = build_cubic_perovskite(**arguments, x_site=x_site)
                else:
                    result = build_ordered_double_perovskite(**arguments, x_site=x_site, b_prime_site=b_prime)
                _save_preview(project, result)
        preview = st.session_state.get(preview_key)
        if preview:
            result = preview['result']
            st.subheader('Uncommitted preview: ' + result.source_label)
            for warning in result.warnings:
                st.warning(warning)
            _view(project, result.atoms, 'source_view')
            if st.button('Apply Structure', key=key('apply_source')):
                entry = structures.commit_structure(project, result.atoms, name=result.source_label,
                    source={'kind': result.source_kind, 'label': result.source_label, 'format': result.source_format,
                            'details': result.source_details, 'warnings': result.warnings}, operation=result.source_kind,
                    original_bytes=preview['original_bytes'], original_filename=preview['original_filename'], identifier=preview['token'])
                st.session_state.pop(preview_key, None)
                st.session_state[key('view_structure')] = entry.id
                st.rerun()
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        st.error(str(exc))

    history = structures.list_structures(project)
    st.subheader('Committed versions')
    if not history:
        st.info('No structures committed yet.')
        return
    lookup = {entry.id: entry for entry in history}
    selected_key = key('view_structure')
    last_created = st.session_state.get(key('last_created_structure'))
    if last_created in lookup:
        st.success(f'Created structure_{lookup[last_created].sequence:03d}. The transformation source and active structure are unchanged.')
        if st.button('View New Version', key=key('view_new')):
            st.session_state[selected_key] = last_created
            st.session_state.pop(key('last_created_structure'), None)
            st.rerun()
    if st.session_state.get(selected_key) not in lookup:
        st.session_state[selected_key] = project.active_structure_id or history[-1].id
    selected = st.selectbox('View structure', list(lookup), format_func=lambda identifier: f'structure_{lookup[identifier].sequence:03d} · {lookup[identifier].name}', key=selected_key)
    entry = lookup[selected]
    st.caption(f'Parent: {entry.parent_structure_id or "none"} · Operation: {entry.operation} · ID: {entry.id}')
    st.json(json.loads(entry.operation_json))
    try:
        atoms = structures.load_structure(project, selected)
        _view(project, atoms, 'history:' + selected)
        if project.active_structure_id == selected:
            st.success('Active structure')
        elif st.button('Set Active', key=key('set_active_structure')):
            structures.set_active_structure(project, selected)
            st.rerun()
        st.subheader('Transformation preview')
        operation = st.selectbox('Operation', ['primitive', 'conventional', 'supercell'], key=key('transform_operation'))
        repeat = (1, 1, 1)
        if operation == 'supercell':
            columns = st.columns(3)
            repeat = tuple(column.number_input('Repeat ' + axis, min_value=1, max_value=3, value=1, step=1, key=key('repeat_' + axis)) for column, axis in zip(columns, 'abc'))
        transformed, parameters = structures.preview_transformation(project, selected, operation, repeat=repeat)
        if parameters.get('site_label_policy'):
            st.caption('Source CIF site labels are regenerated for this cell; original labels remain in the parent version.')
        _view(project, transformed, 'transform:' + selected)
        st.caption('Based on the selected committed version. Controls and viewer changes do not create records.')
        if st.button('Apply Transformation', key=key('apply_transform')):
            preview_identity = json.dumps([selected, operation, parameters], sort_keys=True)
            token = st.session_state.setdefault(key('transform_token:' + preview_identity), str(uuid4()))
            entry = structures.commit_structure(project, transformed, name=f'{operation} of structure_{entry.sequence:03d}',
                parent_id=selected, operation=operation, parameters=parameters, source={'kind': 'transformation'}, identifier=token)
            st.session_state[key('last_created_structure')] = entry.id
            st.rerun()
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        st.error(str(exc))
