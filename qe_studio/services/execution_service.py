"""Durable attempts and scientific validation around the existing local runner."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
from datetime import datetime
from uuid import uuid4
import xml.etree.ElementTree as ET

from ..core.project_models import ProjectError, ProjectConflictError
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import canonical_json
from ..storage.project_repository import project_connection, staged_payload, touch_project
from . import calculation_service as calculations
from .baseline_service import get_baseline, BASELINE_FIELDS
from .pseudo_set_service import load_pseudo_entries

TERMINAL_STATES = {'completed', 'failed', 'cancelled', 'interrupted'}


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def get_run(project, identifier):
    with project_connection(project) as db:
        row = db.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProjectError('This run does not belong to the opened project.')
    return dict(row)


def list_runs(project, calculation_id=None):
    with project_connection(project) as db:
        query = 'SELECT * FROM runs' + (' WHERE calculation_id=?' if calculation_id else '') + ' ORDER BY created_at,attempt'
        return [dict(row) for row in db.execute(query, (calculation_id,) if calculation_id else ())]


def run_steps(project, identifier):
    get_run(project, identifier)
    with project_connection(project) as db:
        return [dict(row) for row in db.execute('SELECT * FROM run_steps WHERE run_id=? ORDER BY ordinal', (identifier,))]


def artifacts(project, identifier):
    get_run(project, identifier)
    with project_connection(project) as db:
        return [dict(row) for row in db.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY role,relpath', (identifier,))]


def content_hash(path):
    if path.is_symlink():
        raise ProjectError('Project execution artifacts cannot use symbolic links.')
    if path.is_file():
        return _file_hash(path)
    if not path.is_dir():
        raise ProjectError(f'Required artifact is missing: {path.name}')
    files = {}
    for child in sorted(path.rglob('*')):
        if child.is_symlink():
            raise ProjectError('Restart trees cannot contain symbolic links.')
        if child.is_file():
            files[child.relative_to(path).as_posix()] = _file_hash(child)
    return hashlib.sha256(canonical_json(files).encode()).hexdigest()


def register_artifact(db, project, calculation_id, run_id, role, path):
    relpath = path.relative_to(project.root_path).as_posix()
    ProjectPaths(project.root_path).resolve_relative(relpath)
    db.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?,?,?,?)',
        (str(uuid4()), calculation_id, run_id, role, relpath, content_hash(path), 'directory' if path.is_dir() else 'file', calculations.now()))


def parent_restart(project, parent_run_id):
    run = get_run(project, parent_run_id)
    if run['state'] != 'completed':
        raise ProjectError('Select a completed parent run.')
    matches = [a for a in artifacts(project, parent_run_id) if a['role'] == 'restart']
    if len(matches) != 1:
        raise ProjectError('The parent run has no registered restart data.')
    source = ProjectPaths(project.root_path).resolve_relative(matches[0]['relpath'])
    if content_hash(source) != matches[0]['sha256']:
        raise ProjectError('The parent restart data changed after completion.')
    return source


def validate_parent(project, calculation):
    expected = {'nscf': 'scf', 'bands': 'scf', 'dos': 'nscf'}
    if calculation.kind not in expected:
        if calculation.parent_run_id:
            raise ProjectError('Only NSCF, bands, and DOS accept a parent run in this phase.')
        return None
    if not calculation.parent_run_id:
        raise ProjectError(f'{calculation.kind.upper()} requires an explicit completed {expected[calculation.kind].upper()} parent run.')
    run = get_run(project, calculation.parent_run_id)
    parent = calculations.get_calculation(project, run['calculation_id'])
    if parent.kind != expected[calculation.kind] or parent.baseline_id != calculation.baseline_id:
        raise ProjectError('Parent kind and baseline must match the selected workflow.')
    request = json.loads(parent.resolved_request_json)
    child = calculations._merged(project, calculation.baseline_id, calculation.kind, json.loads(calculation.overrides_json))
    fixed = BASELINE_FIELDS - {'k_points_grid', 'conv_thr', 'mixing_beta', 'occupations', 'smearing', 'degauss', 'spin_source'}
    if any(child.get(key) != request.get(key) for key in fixed):
        raise ProjectError('Parent structure, UPFs, physical model, and cutoffs must be inherited unchanged. Start a new SCF for those changes.')
    parent_restart(project, calculation.parent_run_id)
    return request


def create_run(project, calculation_id, *, expected_revision, commands=None):
    calculation = calculations.get_calculation(project, calculation_id)
    owner = calculations.study_point_owner(project, calculation_id)
    if owner:
        from .studies.study_service import get_study
        if get_study(project, owner)['state'] != 'generated':
            raise ProjectError('Finish study generation before executing its points.')
    if calculation.draft_revision != expected_revision or calculation.state != 'input_generated':
        raise ProjectConflictError('Input needs regeneration before creating a run.')
    files = calculations.generated_inputs(project, calculation)
    baseline = get_baseline(project, calculation.baseline_id)
    load_pseudo_entries(project, baseline.pseudo_set_id)
    validate_parent(project, calculation)
    metadata = json.loads(files['metadata.json'].read_text())
    specification = metadata['steps']
    for step in specification:
        command = (commands or {}).get(step['executable'], [step['executable']])
        if not isinstance(command, (list, tuple)) or not command or any(not isinstance(arg, str) for arg in command):
            raise ProjectError('Commands must be nonempty argument lists.')
        step['command'] = [*command, '-in', step['input']]
    with staged_payload(project) as stage:
        work = stage / 'work'; work.mkdir()
        for name, path in files.items():
            shutil.copyfile(path, work / name)
            if content_hash(work / name) != json.loads(calculation.input_manifest_json)[name]:
                raise ProjectError('Generated input changed while staging the run.')
        for directory in ('tmp', 'logs', 'results'):
            (work / directory).mkdir()
        if calculation.parent_run_id:
            source = parent_restart(project, calculation.parent_run_id)
            expected_parent_hash = next(a['sha256'] for a in artifacts(project, calculation.parent_run_id) if a['role'] == 'restart')
            shutil.copytree(source, work / 'tmp' / source.name)
            # Verify the copy against the registered immutable parent tree.
            if content_hash(work / 'tmp' / source.name) != expected_parent_hash:
                raise ProjectError('Parent restart data changed while copying.')
            (work / 'logs/parent_restart.json').write_text(canonical_json({
                'parent_run_id': calculation.parent_run_id, 'source_relpath': source.relative_to(project.root_path).as_posix(),
                'staged_relpath': 'tmp/' + source.name, 'sha256': expected_parent_hash}))
        with project_connection(project, write=True) as db:
            latest = db.execute('SELECT * FROM calculations WHERE id=?', (calculation.id,)).fetchone()
            if latest['draft_revision'] != expected_revision or latest['input_relpath'] != calculation.input_relpath:
                raise ProjectConflictError('Calculation changed while staging the run.')
            if db.execute("SELECT 1 FROM runs WHERE calculation_id=? AND state IN ('queued','running')", (calculation.id,)).fetchone():
                raise ProjectError('This calculation already has a queued or running attempt.')
            attempt = db.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM runs WHERE calculation_id=?', (calculation.id,)).fetchone()[0]
            final = calculations.calculation_dir(project, calculation) / 'runs' / f'run_{attempt:03d}'
            while final.exists():
                attempt += 1
                final = final.with_name(f'run_{attempt:03d}')
            relpath = final.relative_to(project.root_path).as_posix()
            ProjectPaths(project.root_path).resolve_relative(relpath)
            identifier = str(uuid4()); timestamp = calculations.now()
            frozen = {**asdict(calculation), 'input_manifest': json.loads(calculation.input_manifest_json)}
            db.execute('''INSERT INTO runs (id,calculation_id,attempt,state,work_relpath,specification_json,frozen_definition_json,created_at)
                VALUES (?,?,?,'queued',?,?,?,?)''',
                (identifier, calculation.id, attempt, relpath, canonical_json(specification), canonical_json(frozen), timestamp))
            for ordinal, step in enumerate(specification, 1):
                db.execute('INSERT INTO run_steps VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)',
                    (identifier, ordinal, step['kind'], canonical_json(step['command']), relpath,
                     relpath + '/' + step['output'], 'queued'))
            final.parent.mkdir(parents=True, exist_ok=True)
            work.rename(final)
            for name in files:
                register_artifact(db, project, calculation.id, identifier, 'input', final / name)
            if calculation.parent_run_id:
                register_artifact(db, project, calculation.id, identifier, 'parent_restart_snapshot', final / 'logs/parent_restart.json')
            (final / 'run.json').write_text(canonical_json({'run_id': identifier, 'calculation_id': calculation.id, 'attempt': attempt,
                'frozen_definition': frozen, 'steps': specification}), encoding='utf-8')
            touch_project(db, project)
    calculations._snapshot(project, calculations.get_calculation(project, calculation_id))
    return get_run(project, identifier)


def validate_step(project, run_id, step):
    run = get_run(project, run_id)
    root = ProjectPaths(project.root_path).resolve_relative(run['work_relpath'])
    output = root / step['output']
    text = output.read_text(errors='replace')
    if 'JOB DONE.' not in text:
        raise ProjectError('QE did not report normal termination (JOB DONE).')
    kind = step['kind']
    result = {'kind': kind, 'normal_termination': True}
    version = re.search(r'Program\s+\S+\s+v\.([^\s]+)', text)
    if version:
        result['qe_version'] = version.group(1)
    request = json.loads(run['frozen_definition_json'])['resolved_request_json']
    request = json.loads(request)
    if kind in {'scf', 'relax', 'vc-relax'}:
        if 'convergence has been achieved' not in text.lower() or 'convergence NOT achieved' in text:
            raise ProjectError('Electronic convergence was not established.')
        matches = re.findall(r'!\s+total energy\s*=\s*([-+\d.eEdD]+)\s+Ry', text)
        if not matches:
            raise ProjectError('QE output has no usable total energy.')
        result['total_energy_ry'] = float(matches[-1].replace('D', 'E').replace('d', 'e'))
        if kind in {'relax','vc-relax'}:
            if not any(marker in text.lower() for marker in ('bfgs converged', 'end of bfgs geometry optimization')):
                raise ProjectError('Ionic convergence was not established.')
            from ase.io import read
            from ..core.structure_identity import snapshot_bytes
            atoms = read(output, format='espresso-out', index=-1)
            (root / 'results' / 'final_structure.json').write_bytes(snapshot_bytes(atoms))
    elif kind in {'nscf', 'bands'}:
        save = root / 'tmp' / (request['prefix'] + '.save')
        xml = save / 'data-file-schema.xml'
        if run['started_at'] and xml.stat().st_mtime_ns < int(datetime.fromisoformat(run['started_at']).timestamp() * 1e9):
            raise ProjectError('QE did not update the inherited eigenvalue data for this attempt.')
        tree = ET.parse(xml)
        if not any(element.tag.split('}')[-1] == 'eigenvalues' for element in tree.iter()):
            raise ProjectError('QE saved data has no eigenvalues.')
    elif kind == 'dos':
        from .postprocessing import validate_dos_output_file
        validate_dos_output_file(root / 'dos.dat')
    elif kind == 'bands_pp':
        from .postprocessing import validate_band_output_file
        validate_band_output_file(root / 'filband.gnu', band_input_file=root / 'pw.in')
    with project_connection(project, write=True) as db:
        old = json.loads(db.execute('SELECT scientific_json FROM runs WHERE id=?', (run_id,)).fetchone()[0])
        old[kind] = result
        db.execute('UPDATE runs SET scientific_json=? WHERE id=?', (canonical_json(old), run_id))


def collect_artifacts(project, run_id):
    run = get_run(project, run_id)
    root = ProjectPaths(project.root_path).resolve_relative(run['work_relpath'])
    request = json.loads(json.loads(run['frozen_definition_json'])['resolved_request_json'])
    with project_connection(project, write=True) as db:
        for step in json.loads(run['specification_json']):
            path = root / step['output']
            if path.is_file():
                register_artifact(db, project, run['calculation_id'], run_id, 'output', path)
        for name in ('dos.dat', 'filband.gnu', 'filband', 'results/final_structure.json', 'logs/events.jsonl', 'exit.json'):
            path = root / name
            if path.is_file():
                register_artifact(db, project, run['calculation_id'], run_id, 'result' if name != 'logs/events.jsonl' else 'log', path)
        saved = root / 'tmp' / (request['prefix'] + '.save')
        if saved.is_dir():
            register_artifact(db, project, run['calculation_id'], run_id, 'restart', saved)
