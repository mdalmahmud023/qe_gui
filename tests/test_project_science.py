from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from ase.build import bulk
from ase.constraints import FixAtoms
from ase.io import write
from ase.spacegroup import Spacegroup
import numpy as np
import pytest

from qe_studio.core.project_models import ProjectError, ProjectConflictError, PROJECT_FORMAT
from qe_studio.core.project_paths import ProjectPaths
from qe_studio.core.project_context import build_project_context
from qe_studio.core.structure_identity import snapshot_bytes, atoms_from_snapshot, structure_hash
from qe_studio.core.legacy_structure_identity import get_structure_signature
from qe_studio.services.project_service import create_project, open_project
from qe_studio.services import structure_service as structures, pseudo_set_service as pseudos, baseline_service as baselines
from qe_studio.services.structure_sources import import_structure_file
from qe_studio.storage.database import connect
from qe_studio.storage import migrations
from qe_studio.storage.project_repository import pending_payloads


@pytest.fixture
def project(tmp_path):
    return create_project(tmp_path, 'Scientific project')


def configuration(project, tmp_path, *, symbol='Si', content='scalar UPF'):
    entry = structures.commit_structure(project, bulk(symbol), name=symbol)
    path = tmp_path / f'{symbol}.UPF'
    path.write_text(content)
    pseudo = pseudos.commit_pseudo_set(project, {symbol: {'local_path': str(path), 'cutoff_wfc': 40, 'cutoff_rho': 160, 'md5': 'supplied-only'}})
    return entry, pseudo


def test_exact_snapshot_roundtrip_metadata_hash_and_restart(project):
    atoms = bulk('Si')
    atoms.positions[1, 0] += 0.12345678901234567
    atoms.set_pbc([True, False, True])
    atoms.set_tags([5, 8])
    atoms.set_initial_magnetic_moments([0.3, -0.3])
    atoms.set_initial_charges([0.1, -0.1])
    atoms.set_constraint(FixAtoms(indices=[0]))
    atoms.set_celldisp([0.1, 0.2, 0.3])
    atoms.info = {'nested': {'array': np.array([0.12345678901234567]), 'tuple': (1, 2)}, 'spacegroup': Spacegroup(1)}
    saved = snapshot_bytes(atoms)
    entry = structures.commit_structure(project, atoms, name='precision')
    project = structures.set_active_structure(open_project(project.root_path), entry.id)
    reopened = open_project(project.root_path)
    loaded = structures.load_structure(reopened, entry.id)
    assert snapshot_bytes(loaded) == saved
    assert loaded.constraints[0].get_indices().tolist() == [0]
    assert isinstance(loaded.info['spacegroup'], Spacegroup)
    assert isinstance(loaded.info['nested']['tuple'], tuple)
    assert entry.structure_hash == hashlib.sha256(saved).hexdigest()
    assert reopened.active_structure_id == entry.id
    assert snapshot_bytes(build_project_context(project=reopened).atoms) == saved
    ordered = bulk('Si'); reverse = ordered[::-1]
    assert structure_hash(ordered) != structure_hash(reverse)
    assert get_structure_signature(ordered)['hash'] == get_structure_signature(reverse)['hash']
    changed = ordered.copy(); changed.pbc[2] = False
    assert structure_hash(changed) != structure_hash(ordered)
    changed = ordered.copy(); changed.set_tags([1, 0])
    assert structure_hash(changed) != structure_hash(ordered)


def test_cif_original_bytes_and_canonical_reload(project):
    original = io.BytesIO()
    write(original, bulk('NaCl', crystalstructure='rocksalt', a=5.6, cubic=True), format='cif')
    payload = original.getvalue()
    imported = import_structure_file(filename='salt.cif', file_bytes=payload)
    entry = structures.commit_structure(project, imported.atoms, name='salt', original_bytes=payload, original_filename='salt.cif')
    root = project.root_path / Path(entry.snapshot_relpath).parent
    assert (root / 'source.cif').read_bytes() == payload
    assert (root / 'structure.cif').exists()
    (root / 'structure.cif').write_text('not authoritative')
    assert snapshot_bytes(structures.load_structure(open_project(project.root_path), entry.id)) == snapshot_bytes(imported.atoms)
    primitive, parameters = structures.preview_transformation(project, entry.id, 'primitive')
    assert len(primitive) == 2
    assert parameters['source_site_labels'] == imported.atoms.arrays['spacegroup_kinds'].tolist()
    assert set(primitive.info['occupancy']) == {'0', '1'}


def test_lineage_preview_adoption_and_previous_version(project):
    first = structures.commit_structure(project, bulk('Si', cubic=True), name='import')
    project = structures.set_active_structure(open_project(project.root_path), first.id)
    for _ in range(3):
        atoms, parameters = structures.preview_transformation(project, first.id, 'primitive')
    assert len(structures.list_structures(project)) == 1
    primitive = structures.commit_structure(project, atoms, name='primitive', parent_id=first.id, operation='primitive', parameters=parameters)
    atoms, parameters = structures.preview_transformation(project, primitive.id, 'supercell', repeat=(2, 2, 1))
    repeated = structures.commit_structure(project, atoms, name='supercell', parent_id=primitive.id, operation='supercell', parameters=parameters)
    history = structures.list_structures(project)
    assert [e.parent_structure_id for e in history] == [None, first.id, primitive.id]
    assert json.loads(repeated.operation_json)['repeat'] == [2, 2, 1]
    assert len(structures.load_structure(project, repeated.id)) == 8
    assert open_project(project.root_path).active_structure_id == first.id
    project = structures.set_active_structure(open_project(project.root_path), repeated.id)
    project = structures.set_active_structure(project, first.id)
    assert open_project(project.root_path).active_structure_id == first.id
    assert len(structures.list_structures(project)) == 3
    conventional, _ = structures.preview_transformation(project, primitive.id, 'conventional')
    assert len(conventional) == 8


def test_standardization_does_not_silently_drop_site_properties(project):
    atoms = bulk('Si'); atoms.set_tags([1, 2])
    entry = structures.commit_structure(project, atoms, name='tagged')
    for op in ('primitive', 'conventional'):
        with pytest.raises(ProjectError, match='site arrays'):
            structures.preview_transformation(project, entry.id, op)
    repeated, _ = structures.preview_transformation(project, entry.id, 'supercell', repeat=(2, 1, 1))
    assert repeated.get_tags().tolist() == [1, 2, 1, 2]
    assert len(structures.list_structures(project)) == 1


def test_pseudo_dedup_hash_cache_deletion_move_and_integrity(project, tmp_path):
    cached = tmp_path / 'cache/Si.UPF'; cached.parent.mkdir(); cached.write_bytes(b'first UPF')
    entries = {'Si': {'local_path': str(cached), 'md5': 'same supplied md5'}}
    first = pseudos.commit_pseudo_set(project, entries)
    assert pseudos.commit_pseudo_set(project, entries).id == first.id
    cached.write_bytes(b'second UPF')
    second = pseudos.commit_pseudo_set(project, entries)
    assert first.pseudo_set_hash != second.pseudo_set_hash
    shutil.rmtree(cached.parent)
    moved = tmp_path / 'moved'; project.root_path.rename(moved); project = open_project(moved)
    loaded = pseudos.load_pseudo_entries(project, first.id)['Si']
    assert Path(loaded['local_path']).read_bytes() == b'first UPF'
    assert loaded['sha256'] == hashlib.sha256(b'first UPF').hexdigest()
    Path(loaded['local_path']).write_bytes(b'changed inside project')
    with pytest.raises(ProjectError, match='changed'):
        pseudos.load_pseudo_entries(project, first.id)
    assert len(pseudos.list_pseudo_sets(project)) == 2


def test_baseline_revisions_exact_ids_and_context(project, tmp_path):
    structure, pseudo = configuration(project, tmp_path)
    settings = baselines.default_baseline_settings(structures.load_structure(project, structure.id))
    first = baselines.commit_baseline(project, structure.id, pseudo.id, settings)
    settings['ecutwfc'] = 70; settings['ecutrho'] = 560; settings['k_points_grid'] = [6, 6, 6]
    second = baselines.commit_baseline(project, structure.id, pseudo.id, settings, previous_baseline_id=first.id)
    assert first.structure_id == structure.id and first.pseudo_set_id == pseudo.id
    assert second.previous_baseline_id == first.id
    assert json.loads(baselines.get_baseline(project, first.id).settings_json)['ecutwfc'] != 70
    project = baselines.set_active_baseline(open_project(project.root_path), second.id)
    reopened = open_project(project.root_path)
    assert reopened.active_baseline_id == second.id and reopened.active_structure_id == structure.id
    context = baselines.baseline_calculation_context(reopened, second.id)
    assert context.numerical.ecutwfc == 70 and tuple(context.physical.k_points_grid) == (6, 6, 6)
    other = structures.commit_structure(project, bulk('Al'), name='other')
    project = structures.set_active_structure(open_project(project.root_path), other.id)
    assert project.active_baseline_id is None
    assert len(baselines.list_baselines(project)) == 2
    project = baselines.set_active_baseline(project, first.id)
    assert project.active_structure_id == structure.id


def test_cross_project_ids_and_species_cannot_leak(project, tmp_path):
    first, pseudo = configuration(project, tmp_path)
    other_project = create_project(tmp_path, 'Other')
    second, other_pseudo = configuration(other_project, tmp_path, symbol='Al', content='other bytes')
    with pytest.raises(ProjectError):
        structures.load_structure(other_project, first.id)
    with pytest.raises(ProjectError):
        pseudos.load_pseudo_entries(project, other_pseudo.id)
    with pytest.raises(ProjectError):
        baselines.commit_baseline(project, first.id, other_pseudo.id, {})
    with pytest.raises(ProjectError):
        structures.commit_structure(other_project, bulk('Si'), name='wrong parent', parent_id=first.id)
    wrong_species = structures.commit_structure(project, bulk('Al'), name='Al')
    with pytest.raises(ProjectError, match='species'):
        baselines.commit_baseline(project, wrong_species.id, pseudo.id, {})


@pytest.mark.parametrize('table', ['structures', 'pseudo_sets', 'pseudo_entries', 'baselines'])
@pytest.mark.parametrize('action', ['UPDATE', 'DELETE'])
def test_scientific_records_immutable_in_sql(project, tmp_path, table, action):
    structure, pseudo = configuration(project, tmp_path)
    baselines.commit_baseline(project, structure.id, pseudo.id, {})
    update_column = {'structures': 'name', 'pseudo_sets': 'settings_json', 'pseudo_entries': 'filename', 'baselines': 'settings_json'}[table]
    sql = f'DELETE FROM {table}' if action == 'DELETE' else f'UPDATE {table} SET {update_column}={update_column}'
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with connect(ProjectPaths(project.root_path).database) as db:
            db.execute(sql)


def test_invalid_baseline_is_not_committed(project, tmp_path):
    structure, pseudo = configuration(project, tmp_path)
    for settings in ({'ecutwfc': -1}, {'ecutrho': float('nan')}, {'k_points_grid': [2.5, 2, 2]}, {'noncolin': True, 'lspinorb': True}, {'prefix': 'not a baseline field'}):
        with pytest.raises(ValueError):
            baselines.commit_baseline(project, structure.id, pseudo.id, settings)
    assert baselines.list_baselines(project) == []


def test_committed_pseudo_mapping_cannot_gain_entries(project, tmp_path):
    _, pseudo = configuration(project, tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with connect(ProjectPaths(project.root_path).database) as db:
            db.execute('INSERT INTO pseudo_entries VALUES (?,?,?,?,?,?)', (pseudo.id, 'Al', 'Al.UPF', 'other', 'hash', '{}'))


def test_payload_symlink_escape_rejected(project, tmp_path):
    entry = structures.commit_structure(project, bulk('Si'), name='Si')
    root = project.root_path / Path(entry.snapshot_relpath).parent
    external = tmp_path / 'external'; root.rename(external); root.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='leaves'):
        structures.load_structure(project, entry.id)


def test_active_pointer_conflict_and_no_in_place_changes(project):
    first = structures.commit_structure(project, bulk('Si'), name='Si')
    second = structures.commit_structure(project, bulk('Al'), name='Al')
    stale_tab = open_project(project.root_path)
    current = structures.set_active_structure(stale_tab, first.id)
    with pytest.raises(ProjectConflictError, match='another tab'):
        structures.set_active_structure(stale_tab, second.id)
    assert open_project(project.root_path).active_structure_id == first.id
    assert structures.get_structure(current, first.id) == first


def test_unsupported_metadata_rejected_before_commit(project):
    atoms = bulk('Si'); atoms.info['unsupported'] = object()
    with pytest.raises(ValueError, match='metadata'):
        structures.commit_structure(project, atoms, name='unsupported')
    assert structures.list_structures(project) == []
    assert not list((project.root_path / '.staging').iterdir())


def test_duplicate_apply_token_and_concurrent_sequences(project):
    token = str(uuid4())
    first = structures.commit_structure(project, bulk('Si'), name='first', identifier=token)
    assert structures.commit_structure(project, bulk('Si'), name='first', identifier=token).id == first.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        entries = list(pool.map(lambda name: structures.commit_structure(project, bulk('Si'), name=name), ['A', 'B']))
    assert len({entry.sequence for entry in [first, *entries]}) == 3


def test_failed_publication_retains_payload_without_success_record(project, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('crash after rename')
    monkeypatch.setattr(structures, 'touch_project', fail)
    with pytest.raises(RuntimeError):
        structures.commit_structure(project, bulk('Si'), name='failed')
    assert structures.list_structures(project) == []
    assert pending_payloads(project) == ['structures/structure_001']
    assert (project.root_path / 'structures/structure_001/atoms.json').is_file()


def make_v1(root):
    root.mkdir(); (root / '.staging').mkdir()
    timestamp = datetime.now(timezone.utc).isoformat(); identifier = str(uuid4())
    with connect(root / 'project.sqlite3', create=True) as db:
        for sql in migrations.SCHEMA_V1:
            db.execute(sql)
        db.execute('INSERT INTO schema_migrations VALUES (1,?,?)', (timestamp, migrations.SCHEMA_V1_CHECKSUM))
        db.execute('INSERT INTO project VALUES (1,?,?,?,?,?,1)', (identifier, 'Old project', 'original', timestamp, timestamp))
        db.execute('PRAGMA user_version=1')
    (root / 'project.json').write_text(json.dumps({'format': PROJECT_FORMAT, 'schema_version': 1, 'project_id': identifier, 'database': 'project.sqlite3'}))
    return identifier


def test_v1_migrates_atomically_preserving_identity(tmp_path):
    root = tmp_path / 'old'; identifier = make_v1(root)
    marker = (root / 'project.json').read_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        projects = list(pool.map(open_project, [root, root]))
    assert all(p.id == identifier and p.schema_version == 2 for p in projects)
    assert (root / 'project.json').read_bytes() == marker
    assert {p.name for p in root.iterdir()} == {'project.json', 'project.sqlite3', '.staging'}
    assert open_project(root) == projects[0]


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    root = tmp_path / 'old'; make_v1(root)
    monkeypatch.setattr(migrations, 'SCHEMA_V2', (*migrations.SCHEMA_V2[:1], 'INVALID SQL'))
    with pytest.raises(ProjectError):
        open_project(root)
    with connect(root / 'project.sqlite3', read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'project', 'schema_migrations'}
