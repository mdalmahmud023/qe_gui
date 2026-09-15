"""Selected-item compatibility, provenance, and independent file diagnostics."""
import streamlit as st

from ..services import dependency_service as dependencies
from ..services.baseline_service import get_baseline
from ..services import calculation_service as calculations, execution_service as execution
from ..services.studies import study_service as studies
from ..services.structure_service import get_structure
from ..services.pseudo_set_service import get_pseudo_set
from ..storage.project_repository import project_connection


def _label(project, kind, identifier):
    if kind == 'baseline':
        return f'baseline_{get_baseline(project, identifier).sequence:03d}'
    if kind == 'calculation':
        return calculations.get_calculation(project, identifier).label
    if kind == 'run':
        run = execution.get_run(project, identifier)
        return f"{calculations.get_calculation(project, run['calculation_id']).label} / Run #{run['attempt']:03d}"
    if kind == 'study_result':
        result = studies.get_result(project, identifier)
        study = studies.get_study(project, result['study_id'])
        return f"{study['kind']}_{study['sequence']:03d} / Result #{result['revision']:03d}"
    if kind == 'study':
        study = studies.get_study(project, identifier)
        return f"{study['kind']}_{study['sequence']:03d}"
    if kind == 'structure':
        return f'structure_{get_structure(project, identifier).sequence:03d}'
    if kind == 'pseudo_set':
        return f'set_{get_pseudo_set(project, identifier).sequence:03d}'
    if kind == 'artifact':
        with project_connection(project) as db:
            row = db.execute('SELECT role,relpath FROM artifacts WHERE id=?', (identifier,)).fetchone()
        return f"{row['role']}: {row['relpath']}" if row else 'Unavailable artifact record'
    return f'{kind}: {identifier}'


def render_assessment(project, kind, identifier):
    report = dependencies.evaluate(project, kind, identifier)
    label = f'Freshness: {report.state.value.capitalize()}'
    if report.state == dependencies.Freshness.CURRENT:
        st.success(label + ' · Matches the selected baseline.')
    elif report.state == dependencies.Freshness.STALE:
        st.warning(label + ' · Incompatible with the selected baseline.')
    else:
        st.info(label + ' · Compatibility is not established.')
    if report.baseline_id:
        st.caption(f'Compared with {_label(project, "baseline", report.baseline_id)} · freshness policy {report.policy_version}')
    with st.expander('Compatibility, provenance, and file checks'):
        for reason in report.reasons:
            st.write('• ' + reason.message)
        if report.dependencies:
            st.dataframe([{'Relationship': link.relationship.value,
                'Source': _label(project, link.upstream_kind, link.upstream_id),
                'Fingerprint': link.fingerprint or '—'} for link in report.dependencies], hide_index=True)
        if report.files:
            st.dataframe([{'Artifact': f.role, 'Path': f.relpath or '—',
                'Availability': f.availability, 'Integrity': f.integrity} for f in report.files], hide_index=True)
        for capability, ready in report.capabilities.items():
            st.write(capability.replace('_', ' ').capitalize() + ': ' + ('available' if ready else 'unavailable'))
    # Integrity errors remain prominent even when provenance details are collapsed.
    for check in report.files:
        if check.integrity == 'corrupt':
            st.error(f'Integrity problem: {check.role}. {check.message}')
            st.code(f'Expected: {check.expected or "unknown"}\nFound: {check.found or "unavailable"}')
    if any(f.availability != 'available' for f in report.files):
        st.warning('Some payloads are unavailable. File checks describe missing capabilities separately from scientific freshness.')
    return report


def render_baseline_provenance(project, identifier):
    links = dependencies.baseline_provenance(project, identifier)
    if links:
        st.caption('Recorded scientific origins')
        st.dataframe([{'Relationship': link.relationship.value, 'Fields': ', '.join(link.fields) or 'structure',
            'Source': _label(project, link.upstream_kind, link.upstream_id)} for link in links], hide_index=True)
