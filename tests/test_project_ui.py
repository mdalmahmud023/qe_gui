from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from qe_studio.core import project_session as session
from qe_studio.services.project_service import create_project, open_project
from qe_studio.storage.workspace_store import WorkspaceStore


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('QE_STUDIO_WORKSPACE_DIR', str(tmp_path / 'machine'))
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / 'qe_runs'
    sentinel.mkdir()
    (sentinel / 'keep').write_text('legacy scientific payload')
    application = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=15)
    application.run()
    assert not application.exception
    yield application
    assert (sentinel / 'keep').read_text() == 'legacy scientific payload'
    assert {p.name for p in sentinel.iterdir()} == {'keep'}


def button(app, label):
    return next(b for b in app.button if b.label == label)


def open_in_ui(app, root):
    app.text_input(key=session.PREFIX + 'open_root').set_value(str(root))
    button(app, 'Open Project').click().run()
    assert not app.exception


def test_create_rename_close_and_reopen(app, tmp_path):
    app.text_input(key=session.PREFIX + 'create_name').set_value('First project')
    app.text_input(key=session.PREFIX + 'create_parent').set_value(str(tmp_path / 'projects'))
    button(app, 'Create Project').click().run()
    assert not app.exception
    project = open_project(tmp_path / 'projects/First-project')
    assert app.session_state[session.ACTIVE_KEY] == project.id
    assert app.title[0].value == 'First project'
    assert not app.get('file_uploader')
    app.text_input[0].set_value('Renamed display')
    button(app, 'Save Project Details').click().run()
    assert not app.exception
    assert app.title[0].value == 'Renamed display'
    assert open_project(project.root_path).id == project.id
    button(app, 'Close Project').click().run()
    assert app.title[0].value == 'Projects'
    button(app, 'Open').click().run()
    assert not app.exception and app.title[0].value == 'Renamed display'
    assert {p.name for p in project.root_path.iterdir()} == {'project.json', 'project.sqlite3', '.staging'}


def test_sequential_projects_and_legacy_session_isolation(app, tmp_path):
    a = create_project(tmp_path, 'Same')
    b = create_project(tmp_path, 'Same')
    button(app, 'Legacy Workspace').click().run()
    assert not app.exception
    assert app.session_state[session.ACTIVE_KEY] == session.LEGACY
    app.session_state['qe_parameters'] = {'ecutwfc': 123}
    app.session_state['structure_filename'] = 'legacy.cif'
    button(app, 'Projects').click().run()
    open_in_ui(app, a.root_path)
    assert 'qe_parameters' not in app.session_state
    assert 'structure_filename' not in app.session_state
    app.text_input[0].set_value('unsaved a draft').run()
    button(app, 'Projects').click().run()
    open_in_ui(app, b.root_path)
    assert app.text_input[0].value == 'Same'
    assert app.session_state[session.ACTIVE_KEY] == b.id
    button(app, 'Legacy Workspace').click().run()
    assert not app.exception
    assert app.session_state['qe_parameters']['ecutwfc'] == 123
    assert app.session_state['structure_filename'] == 'legacy.cif'


def test_two_tabs_reject_lost_rename_and_reload(app, tmp_path):
    project = create_project(tmp_path, 'original')
    open_in_ui(app, project.root_path)
    tab2 = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=15).run()
    open_in_ui(tab2, project.root_path)
    app.text_input[0].set_value('tab one')
    button(app, 'Save Project Details').click().run()
    tab2.text_input[0].set_value('tab two')
    button(tab2, 'Save Project Details').click().run()
    assert not tab2.exception
    assert any('another tab' in e.value for e in tab2.error)
    assert open_project(project.root_path).display_name == 'tab one'
    button(tab2, 'Reload Project Details').click().run()
    assert tab2.text_input[0].value == 'tab one'
    tab2.text_input[0].set_value('tab two after reload')
    button(tab2, 'Save Project Details').click().run()
    assert not tab2.exception
    app.run()
    assert app.title[0].value == 'tab two after reload'


def test_missing_project_cannot_fall_back_to_legacy(app, tmp_path):
    project = create_project(tmp_path, 'move me')
    open_in_ui(app, project.root_path)
    moved = tmp_path / 'moved'
    project.root_path.rename(moved)
    app.run()
    assert not app.exception
    assert any('missing' in e.value for e in app.error)
    assert app.session_state[session.ACTIVE_KEY] == project.id
    button(app, 'Projects').click().run()
    assert any('Location missing' in c.value for c in app.caption)
    open_in_ui(app, moved)
    assert app.session_state[session.ACTIVE_KEY] == project.id
    assert len(WorkspaceStore().list_recent()) == 1


def test_corrupt_existing_shows_error(app, tmp_path):
    project = create_project(tmp_path, 'broken')
    (project.root_path / 'project.sqlite3').write_bytes(b'corrupt')
    open_in_ui(app, project.root_path)
    assert app.error and app.session_state[session.ACTIVE_KEY] == session.MANAGER


def test_tabs_can_view_different_projects(app, tmp_path):
    a = create_project(tmp_path, 'Same')
    b = create_project(tmp_path, 'Same')
    open_in_ui(app, a.root_path)
    tab2 = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=15).run()
    open_in_ui(tab2, b.root_path)
    app.text_input[0].set_value('only A')
    button(app, 'Save Project Details').click().run()
    tab2.run()
    assert tab2.title[0].value == 'Same'
    assert app.session_state[session.ACTIVE_KEY] == a.id
    assert tab2.session_state[session.ACTIVE_KEY] == b.id
    button(app, 'Close Project').click().run()
    tab2.run()
    assert tab2.session_state[session.ACTIVE_KEY] == b.id


def test_recents_failure_does_not_lose_created_project(app, tmp_path, monkeypatch):
    from qe_studio.core.project_models import ProjectError
    def fail(*args):
        raise ProjectError('Recents unavailable')
    monkeypatch.setattr(WorkspaceStore, 'remember', fail)
    app.text_input(key=session.PREFIX + 'create_name').set_value('Saved project')
    app.text_input(key=session.PREFIX + 'create_parent').set_value(str(tmp_path))
    button(app, 'Create Project').click().run()
    assert not app.exception
    assert app.title[0].value == 'Saved project'
    assert any('Recents unavailable' in w.value for w in app.warning)
    assert open_project(tmp_path / 'Saved-project').id == app.session_state[session.ACTIVE_KEY]
