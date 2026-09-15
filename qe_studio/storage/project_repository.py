"""Project repositories; SQLite is the metadata authority."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from uuid import uuid4

from ..core.project_models import ProjectError, ProjectConflictError
from ..core.project_paths import ProjectPaths
from ..services.project_service import open_project
from .database import connect

TABLES = {'structures', 'pseudo_sets', 'baselines', 'calculations'}


@contextmanager
def project_connection(project, *, write=False):
    current = open_project(project.root_path)
    if current.id != project.id:
        raise ProjectError('The opened location belongs to a different project.')
    with connect(ProjectPaths(current.root_path).database, read_only=not write) as db:
        if write:
            db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT id FROM project').fetchone()[0] != project.id:
            raise ProjectError('Project identity changed while opening the database.')
        yield db


def records(project, table, model):
    if table not in TABLES:
        raise ValueError('Unknown scientific table')
    with project_connection(project) as db:
        return [model(**dict(row)) for row in db.execute(f'SELECT * FROM {table} ORDER BY sequence')]


def record(project, table, identifier, model):
    if table not in TABLES:
        raise ValueError('Unknown scientific table')
    with project_connection(project) as db:
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProjectError(f'The selected {table} record does not belong to this project.')
    return model(**dict(row))


def touch_project(db, project, *, expected=False, **pointers):
    row = db.execute('SELECT * FROM project').fetchone()
    if expected and row['modified_at'] != project.modified_at.isoformat():
        raise ProjectConflictError('Project changed in another tab. Reload before applying this selection.')
    if not pointers.keys() <= {'active_structure_id', 'active_baseline_id'}:
        raise ValueError('Unknown project pointer')
    now = max(datetime.now(timezone.utc), datetime.fromisoformat(row['modified_at']) + timedelta(microseconds=1)).isoformat()
    fields = {'modified_at': now, **pointers}
    db.execute('UPDATE project SET ' + ', '.join(f'{key}=?' for key in fields), tuple(fields.values()))


def allocate(db, project, table, category, stem):
    if table not in TABLES:
        raise ValueError('Unknown scientific table')
    sequence = db.execute(f'SELECT COALESCE(MAX(sequence),0)+1 FROM {table}').fetchone()[0]
    paths = ProjectPaths(project.root_path)
    while paths.resolve_relative(f'{category}/{stem}_{sequence:03d}').exists():
        # Preserve a payload left by a crash between publication and DB commit.
        sequence += 1
    destination = paths.resolve_relative(f'{category}/{stem}_{sequence:03d}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    return sequence, destination


@contextmanager
def staged_payload(project):
    stage = ProjectPaths(project.root_path).resolve_relative(f'.staging/{uuid4()}')
    stage.mkdir(parents=True)
    try:
        yield stage
    except Exception:
        # Retain interrupted scientific payloads for inspection; no record is committed.
        raise
    else:
        if stage.exists():
            shutil.rmtree(stage)


def pending_payloads(project):
    """Surface preserved staging/orphan directories without claiming them as records."""
    paths = ProjectPaths(project.root_path)
    with project_connection(project) as db:
        owned = {str(Path(row[0]).parent) for row in db.execute('SELECT snapshot_relpath FROM structures')}
        owned.update(str(Path(row[0]).parent) for row in db.execute('SELECT manifest_relpath FROM pseudo_sets'))
        calculation_roots = {f"calculations/{row['kind']}_{row['sequence']:03d}" for row in db.execute('SELECT kind,sequence FROM calculations')}
        owned.update(calculation_roots)
        owned.update(row[0] for row in db.execute('SELECT work_relpath FROM runs'))
        study_roots = {f"studies/{row['kind']}/{row['kind']}_{row['sequence']:03d}" for row in db.execute('SELECT kind,sequence FROM studies')}
        owned.update(study_roots)
        owned.update(str(Path(row[0]).parent) for row in db.execute('SELECT payload_relpath FROM study_results'))
    candidates = []
    directories = [paths.staging_dir, paths.structures_dir, paths.pseudo_base_dir, paths.resolve_relative('calculations')]
    directories.extend(paths.resolve_relative(root + '/runs') for root in calculation_roots)
    directories.extend(paths.resolve_relative('studies/' + kind) for kind in ('cutoff','kpoint','eos'))
    directories.extend(paths.resolve_relative(root + '/results') for root in study_roots)
    for directory in directories:
        if directory.exists():
            for child in directory.iterdir():
                if child.is_dir() and str(child.relative_to(project.root_path)) not in owned:
                    candidates.append(child.relative_to(project.root_path).as_posix())
    return sorted(candidates)
