import json
from pathlib import Path
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from qe_studio.core.project_models import ProjectError, ProjectConflictError
from qe_studio.core.project_paths import ProjectPaths
from qe_studio.services import project_service as service
from qe_studio.storage.database import connect
from qe_studio.storage.workspace_store import WorkspaceStore


def inventory(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_create_reopen_minimal_project(tmp_path):
    project = service.create_project(tmp_path, 'Na₂MgCdF₆ study', 'A description')
    assert service.open_project(project.root_path) == project
    assert project.created_at.tzinfo is not None
    assert project.created_at == project.modified_at
    assert project.description == 'A description'
    assert {p.name for p in project.root_path.iterdir()} == {'project.json', 'project.sqlite3', '.staging'}
    with connect(ProjectPaths(project.root_path).database, read_only=True) as db:
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            'project', 'schema_migrations', 'structures', 'pseudo_sets', 'pseudo_entries', 'baselines'}
        assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    assert not ProjectPaths(tmp_path / 'unused').root_path.exists()


def test_duplicate_names_and_rename_do_not_define_identity(tmp_path):
    a = service.create_project(tmp_path, 'Same name')
    b = service.create_project(tmp_path, 'Same name')
    assert a.id != b.id and a.root_path != b.root_path
    marker = ProjectPaths(a.root_path).marker.read_bytes()
    renamed = service.rename_project(a, b.display_name, description='updated')
    assert renamed.id == a.id and renamed.root_path == a.root_path
    assert renamed.created_at == a.created_at and renamed.modified_at > a.modified_at
    assert ProjectPaths(a.root_path).marker.read_bytes() == marker
    assert service.open_project(b.root_path) == b


def test_two_tabs_create_same_name_concurrently(tmp_path, monkeypatch):
    barrier = Barrier(2)
    original_mkdir = Path.mkdir
    def synchronized_mkdir(path, *args, **kwargs):
        if path.name == 'Race':
            barrier.wait(timeout=5)
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', synchronized_mkdir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: service.create_project(tmp_path, 'Race'), range(2)))
    assert a.id != b.id and a.root_path != b.root_path
    assert service.open_project(a.root_path) == a
    assert service.open_project(b.root_path) == b


def test_moving_directory_updates_same_recent_identity(tmp_path):
    store = WorkspaceStore(tmp_path / 'workspace/index.sqlite3')
    a = service.create_project(tmp_path, 'original')
    store.remember(a)
    moved = tmp_path / 'renamed folder'
    a.root_path.rename(moved)
    assert not store.list_recent()[0].available
    with pytest.raises(ProjectError, match='missing'):
        service.open_project(a.root_path)
    reopened = service.open_project(moved)
    assert reopened.id == a.id
    store.remember(reopened)
    recent, = store.list_recent()
    assert recent.root_path == moved and recent.available
    assert recent.project_id == a.id
    renamed = service.rename_project(reopened, 'New display')
    store.remember(renamed)
    assert store.list_recent()[0].display_name == 'New display'


def test_live_uuid_copy_not_silently_reindexed(tmp_path):
    a = service.create_project(tmp_path, 'original')
    store = WorkspaceStore(tmp_path / 'index.sqlite3')
    store.remember(a)
    shutil.copytree(a.root_path, tmp_path / 'copy')
    with pytest.raises(ProjectConflictError):
        store.remember(service.open_project(tmp_path / 'copy'))
    assert store.list_recent()[0].root_path == a.root_path


def test_concurrent_tabs_rename_conflict(tmp_path):
    a = service.create_project(tmp_path, 'original')
    stale_tab = service.open_project(a.root_path)
    updated = service.rename_project(a, 'tab one')
    with pytest.raises(ProjectConflictError, match='another tab'):
        service.rename_project(stale_tab, 'tab two')
    assert service.open_project(a.root_path) == updated
    def save(name):
        try:
            return service.rename_project(updated, name)
        except ProjectConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['three', 'four']))
    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize('damage', ['bytes', 'newer', 'older', 'migration', 'extra_table', 'missing_table', 'uuid', 'marker', 'missing_database'])
def test_reject_invalid_project_without_mutation(tmp_path, damage):
    project = service.create_project(tmp_path, 'test')
    paths = ProjectPaths(project.root_path)
    if damage == 'bytes':
        paths.database.write_bytes(b'not sqlite')
    elif damage == 'missing_database':
        paths.database.unlink()
    elif damage in {'uuid', 'marker'}:
        marker = json.loads(paths.marker.read_text())
        marker['project_id' if damage == 'uuid' else 'schema_version'] = 'invalid'
        paths.marker.write_text(json.dumps(marker))
    else:
        statements = {'newer': 'PRAGMA user_version=3', 'older': 'PRAGMA user_version=0',
                      'migration': "UPDATE schema_migrations SET checksum='broken'", 'extra_table': 'CREATE TABLE science (id TEXT)',
                      'missing_table': 'DROP TABLE project'}
        with connect(paths.database) as db:
            db.execute(statements[damage])
    before = inventory(project.root_path)
    with pytest.raises(ProjectError):
        service.open_project(project.root_path)
    assert inventory(project.root_path) == before


@pytest.mark.parametrize('stage', ['schema', 'marker', 'validation'])
def test_failed_creation_rolls_back_only_owned_files(tmp_path, monkeypatch, stage):
    sentinel = tmp_path / 'unrelated'
    sentinel.write_text('preserve')
    def fail(*args, **kwargs):
        raise OSError('injected failure')
    if stage == 'schema':
        monkeypatch.setattr(service, 'initialize_project_schema', fail)
    elif stage == 'validation':
        monkeypatch.setattr(service, 'open_project', fail)
    else:
        monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(ProjectError, match='injected failure'):
        service.create_project(tmp_path / 'new/parent', 'project')
    assert {p.name for p in tmp_path.iterdir()} == {'unrelated'}
    assert sentinel.read_text() == 'preserve'


def test_existing_directory_and_external_paths_not_overwritten(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'data').write_text('science')
    before = inventory(tmp_path)
    for folder in ['existing', '..', '../external', '/absolute']:
        with pytest.raises(ProjectError):
            service.create_project(tmp_path, 'Name', folder_name=folder)
    assert inventory(tmp_path) == before


def test_moved_project_with_reused_old_directory(tmp_path):
    project = service.create_project(tmp_path, 'original')
    store = WorkspaceStore(tmp_path / 'index.sqlite3')
    store.remember(project)
    project.root_path.rename(tmp_path / 'moved')
    project.root_path.mkdir()
    (project.root_path / 'unrelated').write_text('preserve')
    store.remember(service.open_project(tmp_path / 'moved'))
    assert store.list_recent()[0].root_path == tmp_path / 'moved'
    assert (project.root_path / 'unrelated').read_text() == 'preserve'


@pytest.mark.parametrize('name', ['', '  ', 'a' * 201, 12])
def test_invalid_names_leave_no_project(tmp_path, name):
    with pytest.raises(ProjectError):
        service.create_project(tmp_path, name)
    assert not list(tmp_path.iterdir())


def test_database_transaction_rolls_back(tmp_path):
    project = service.create_project(tmp_path, 'test')
    with pytest.raises(RuntimeError):
        with connect(ProjectPaths(project.root_path).database) as db:
            db.execute("UPDATE project SET display_name='uncommitted'")
            raise RuntimeError('stop')
    assert service.open_project(project.root_path) == project


def test_recents_multiple_connections_and_separate_storage(tmp_path):
    projects = [service.create_project(tmp_path, f'project {i}') for i in range(3)]
    database = tmp_path / 'machine/index.sqlite3'
    assert WorkspaceStore(database).list_recent() == [] and not database.exists()
    before = [inventory(p.root_path) for p in projects]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda p: WorkspaceStore(database).remember(p), projects))
    assert {r.project_id for r in WorkspaceStore(database).list_recent()} == {p.id for p in projects}
    assert [inventory(p.root_path) for p in projects] == before
    database.write_bytes(b'corrupt')
    with pytest.raises(ProjectError):
        WorkspaceStore(database).list_recent()
    with pytest.raises(ProjectError):
        WorkspaceStore(database).remember(projects[0])


def test_newer_recents_index_not_modified(tmp_path):
    project = service.create_project(tmp_path, 'test')
    store = WorkspaceStore(tmp_path / 'index.sqlite3')
    store.remember(project)
    with connect(store.database) as db:
        db.execute('PRAGMA user_version=2')
    before = store.database.read_bytes()
    with pytest.raises(ProjectError):
        store.remember(project)
    assert store.database.read_bytes() == before
