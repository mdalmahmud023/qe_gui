import pytest
from ase.build import bulk

from qe_studio.core import project_session as session
from qe_studio.core.project_context import build_project_context
from qe_studio.core.project_paths import ProjectPaths
from qe_studio.services.project_service import create_project


def test_switch_banks_science_without_widget_or_runtime_leakage():
    state = {'structure_atoms': bulk('Si'), 'qe_parameters': {'ecutwfc': 30},
             'active_run_id': 'live-run', 'study_active_run_id': 'study-run',
             'structure_file_uploader_0': b'widget bytes', 'unknown_widget': 'old value'}
    session.activate_context(state, 'uuid-a', '/first')
    assert 'structure_atoms' not in state and 'qe_parameters' not in state
    assert 'unknown_widget' not in state and 'active_run_id' not in state
    state['qe_parameters'] = {'ecutwfc': 80}
    state[session.widget_key('uuid-a', 'draft')] = 'private'
    session.activate_context(state, 'uuid-b', '/second')
    assert 'qe_parameters' not in state
    assert session.widget_key('uuid-a', 'draft') not in state
    session.activate_context(state, 'uuid-a', '/moved')
    assert state['qe_parameters'] == {'ecutwfc': 80}
    session.activate_context(state, session.LEGACY)
    assert state['qe_parameters'] == {'ecutwfc': 30}
    assert state['active_run_id'] == 'live-run' and state['study_active_run_id'] == 'study-run'
    assert len(state['structure_atoms']) == 2
    assert 'structure_file_uploader_0' not in state


def test_tab_banks_are_independent_and_close_does_not_destroy_drafts():
    tabs = [{}, {}]
    for i, tab in enumerate(tabs):
        session.activate_context(tab, 'same-uuid')
        tab['qe_parameters'] = {'ecutwfc': i}
        session.activate_context(tab, session.MANAGER)
    for i, tab in enumerate(tabs):
        session.activate_context(tab, 'same-uuid')
        assert tab['qe_parameters'] == {'ecutwfc': i}


def test_opened_context_has_identity_without_science_or_runtime_access(tmp_path, monkeypatch):
    project = create_project(tmp_path, 'Project')
    def forbidden(*args, **kwargs):
        raise AssertionError('Project overview must not access legacy science/runtime')
    monkeypatch.setattr('qe_studio.core.project_context.find_available_analysis_files', forbidden)
    monkeypatch.setattr('qe_studio.core.project_context.reconcile_run_state', forbidden)
    context = build_project_context(project=project)
    assert context.project == project and context.atoms is None and context.analysis_files is None
    assert context.paths == ProjectPaths(project.root_path)
    with pytest.raises(ValueError, match='root'):
        build_project_context(tmp_path / 'foreign', project=project)
