"""Project studies describe experiments; their SCF calculations own all execution.

Generation is resumable: membership is committed point by point under a local
study lock. Once generation starts, the specification cannot change. Failed
generation retains its records and can resume without replacing generated points.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from ...core.project_models import ProjectError, ProjectConflictError
from ...core.project_paths import ProjectPaths
from ...core.structure_identity import canonical_json
from ...storage.project_repository import project_connection, staged_payload, touch_project
from ...convergence.models import CutoffSweepSpec, KPointSweepSpec, EosSweepSpec, CutoffStudyPoint, KPointStudyPoint, PointResult
from ...convergence.cutoff_study import resolve_point_ecutrho
from ...convergence.recommendation import recommend_cutoffs, recommend_kpoints
from ...convergence.result_parser import parse_cutoff_point_output
from ...convergence.shared import attach_successive_energy_deltas
from ...convergence.eos_fit import fit_eos
from .. import calculation_service as calculations, execution_service as execution
from ..baseline_service import get_baseline, commit_baseline
from ..structure_service import load_structure, commit_structure
from ..pseudo_set_service import load_pseudo_entries

KINDS = ('cutoff', 'kpoint', 'eos')
DEFAULT_SPECS = {
    'cutoff': dict(start_ecutwfc_ry=40, stop_ecutwfc_ry=80, step_ry=10,
                   tolerance_mev_per_atom=1, ecutrho_mode='ratio_rule', ratio_rule='8x'),
    'kpoint': dict(mode='3d_isotropic', start_value=2, stop_value=6, step=2, tolerance_mev_per_atom=1),
    'eos': dict(mode='isotropic_scale', start_scale=.94, stop_scale=1.06, num_points=5, fit_model='birch_murnaghan'),
}


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def get_study(project, identifier):
    with project_connection(project) as db:
        row = db.execute('SELECT * FROM studies WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ProjectError('The study does not belong to this project.')
    return dict(row)


def list_studies(project):
    with project_connection(project) as db:
        return [dict(row) for row in db.execute('SELECT * FROM studies ORDER BY created_at')]


def study_dir(project, study):
    return ProjectPaths(project.root_path).resolve_relative(f"studies/{study['kind']}/{study['kind']}_{study['sequence']:03d}")


@contextmanager
def _study_lock(project, identifier):
    get_study(project, identifier)
    path = ProjectPaths(project.root_path).resolve_relative('.staging/study-' + identifier + '.lock')
    with path.open('a+') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProjectConflictError('This study is being updated in another tab. Try again after it finishes.') from exc
        yield


def _positive_values(values, label):
    if not isinstance(values, (list, tuple)) or not values or len(values) > 1001:
        raise ProjectError(f'{label} must contain between 1 and 1001 values.')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        raise ProjectError(f'{label} must contain finite positive numbers.')
    return list(values)


def normalize_spec(project, kind, baseline_id, specification):
    if kind not in KINDS or not isinstance(specification, dict):
        raise ProjectError('Choose a supported study kind and a specification object.')
    canonical_json(specification)
    baseline = get_baseline(project, baseline_id)
    points = []
    if kind == 'cutoff':
        options = dict(specification)
        values = options.pop('ecutwfc_values_ry', None)
        spec = CutoffSweepSpec(**{**DEFAULT_SPECS[kind], **options})
        spec.validate()
        values = _positive_values(values if values is not None else spec.ecutwfc_values(), 'Cutoff grid')
        if values != sorted(set(values)):
            raise ProjectError('Cutoff values must be distinct and increasing.')
        entries = load_pseudo_entries(project, baseline.pseudo_set_id)
        for value in values:
            rho = resolve_point_ecutrho(spec, ecutwfc_ry=value, resolved_entries=entries)
            points.append({'values': {'ecutwfc_ry': value, 'ecutrho_ry': rho}, 'overrides': {'ecutwfc': value, 'ecutrho': rho}})
    elif kind == 'kpoint':
        options = dict(specification)
        grids = options.pop('grids', None)
        spec = KPointSweepSpec(**{**DEFAULT_SPECS[kind], **options})
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (spec.start_value, spec.stop_value, spec.step)):
            raise ProjectError('K-point sweep bounds and step must be integers.')
        spec.validate()
        grids = grids if grids is not None else [spec.generate_grid_for_value(v) for v in spec.k_point_values()]
        if not isinstance(grids, (list, tuple)) or not 1 <= len(grids) <= 1001:
            raise ProjectError('Provide between 1 and 1001 k-point grids.')
        seen = set()
        for grid in grids:
            grid = _positive_values(grid, 'K-point grid')
            if len(grid) != 3 or any(int(n) != n for n in grid):
                raise ProjectError('Each k-point grid needs three positive integers.')
            grid = tuple(int(n) for n in grid)
            if grid in seen:
                continue  # Identity follows the effective grid, including 0D Gamma duplicates.
            if points and any(a > b for a, b in zip(points[-1]['values']['k_points_grid'], grid)):
                raise ProjectError('Resolved k-point grids must increase without decreasing any axis.')
            seen.add(grid)
            points.append({'values': {'k_points_grid': list(grid), 'grid_label': 'x'.join(map(str, grid))},
                           'overrides': {'k_points_grid': list(grid)}})
    else:
        options = dict(specification)
        scales = options.pop('scales', None)
        spec = EosSweepSpec(**{**DEFAULT_SPECS[kind], **options})
        if isinstance(spec.num_points, bool) or not isinstance(spec.num_points, int) or not 4 <= spec.num_points <= 1001:
            raise ProjectError('EOS requires between 4 and 1001 points.')
        spec.validate()
        if spec.fit_model != 'birch_murnaghan':
            raise ProjectError('Only the existing Birch–Murnaghan fit is supported.')
        scales = _positive_values(scales if scales is not None else spec.scale_values(), 'EOS scales')
        if len(scales) < 4 or scales != sorted(set(scales)):
            raise ProjectError('EOS requires at least four distinct, increasing scales.')
        reference = load_structure(project, baseline.structure_id)
        for scale in scales:
            points.append({'values': {'scale_factor': scale, 'volume': float(reference.get_volume()) * scale ** 3}, 'overrides': {}})
    defaults = json.loads(baseline.settings_json)
    for point in points:
        point['overrides'] = {k: v for k, v in point['overrides'].items() if defaults.get(k) != v}
        point['key'] = _hash(point['values'])
    return {'version': 1, 'request': specification, 'sweep': asdict(spec), 'points': points,
            'reference_structure_id': baseline.structure_id, 'pseudo_set_id': baseline.pseudo_set_id}


def create_study(project, kind, baseline_id, specification, *, display_name=None, duplicated_from_id=None):
    spec = normalize_spec(project, kind, baseline_id, specification)
    if duplicated_from_id:
        get_study(project, duplicated_from_id)
    with project_connection(project, write=True) as db:
        sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM studies WHERE kind=?', (kind,)).fetchone()[0]
        while ProjectPaths(project.root_path).resolve_relative(f'studies/{kind}/{kind}_{sequence:03d}').exists():
            sequence += 1
        identifier = str(uuid4()); timestamp = calculations.now()
        name = (display_name or f'{kind}_{sequence:03d}').strip()
        if not name:
            raise ProjectError('Study name cannot be empty.')
        db.execute("INSERT INTO studies VALUES (?,?,?,?,?,?,?,1,'draft',NULL,?,?,?)",
                   (identifier, kind, sequence, name, baseline_id, canonical_json(spec), _hash(spec), duplicated_from_id, timestamp, timestamp))
        touch_project(db, project)
    return get_study(project, identifier)


def save_draft(project, identifier, specification, *, expected_revision):
    study = get_study(project, identifier)
    if study['state'] != 'draft':
        raise ProjectError('Duplicate the study to change its generated specification.')
    spec = normalize_spec(project, study['kind'], study['baseline_id'], specification)
    with project_connection(project, write=True) as db:
        cursor = db.execute("UPDATE studies SET spec_json=?,spec_hash=?,draft_revision=draft_revision+1,modified_at=? WHERE id=? AND draft_revision=? AND state='draft'",
            (canonical_json(spec), _hash(spec), calculations.now(), identifier, expected_revision))
        if cursor.rowcount != 1:
            raise ProjectConflictError('Study changed in another tab. Reload before saving.')
        touch_project(db, project)
    return get_study(project, identifier)


def duplicate_study(project, identifier):
    study = get_study(project, identifier)
    return create_study(project, study['kind'], study['baseline_id'], json.loads(study['spec_json'])['request'],
                        display_name=study['display_name'] + ' (copy)', duplicated_from_id=identifier)


def study_points(project, identifier):
    get_study(project, identifier)
    with project_connection(project) as db:
        return [dict(row) for row in db.execute('SELECT * FROM study_points WHERE study_id=? ORDER BY ordinal', (identifier,))]


def generate_study(project, identifier, *, expected_revision):
    with _study_lock(project, identifier):
        study = get_study(project, identifier)
        if study['draft_revision'] != expected_revision:
            raise ProjectConflictError('Study changed. Reload before generating.')
        if study['state'] == 'generated':
            return study
        with project_connection(project, write=True) as db:
            cursor = db.execute("UPDATE studies SET state='generating' WHERE id=? AND draft_revision=?", (identifier, expected_revision))
            if cursor.rowcount != 1:
                raise ProjectConflictError('Study changed during generation.')
        spec = json.loads(study['spec_json'])
        baseline = get_baseline(project, study['baseline_id'])
        reference = load_structure(project, baseline.structure_id)
        members = {p['ordinal']: p for p in study_points(project, identifier)}
        for ordinal, point in enumerate(spec['points'], 1):
            member = members.get(ordinal)
            if member:
                calculation = calculations.get_calculation(project, member['calculation_id'])
            else:
                calculation_id = str(uuid5(UUID(identifier), 'calculation:' + point['key']))
                with project_connection(project) as db:
                    existing = db.execute('SELECT id FROM calculations WHERE id=?', (calculation_id,)).fetchone()
                if existing:
                    calculation = calculations.get_calculation(project, calculation_id)
                    with project_connection(project, write=True) as db:
                        db.execute('INSERT INTO study_points VALUES (?,?,?,?,?)',
                            (identifier, ordinal, point['key'], calculation.id, canonical_json(point['values'])))
                    if calculation.state != 'input_generated':
                        calculations.generate_calculation(project, calculation.id, expected_revision=calculation.draft_revision)
                    continue
                point_baseline = baseline
                if study['kind'] == 'eos':
                    atoms = reference.copy()
                    atoms.set_cell(reference.cell * point['values']['scale_factor'], scale_atoms=True)
                    structure = commit_structure(project, atoms, name=f"{study['display_name']} point {ordinal}",
                        parent_id=baseline.structure_id, operation='eos_scale', source={'study_id': identifier},
                        parameters={'scale_factor': point['values']['scale_factor'], 'point_key': point['key']},
                        identifier=str(uuid5(UUID(identifier), point['key'])))
                    with project_connection(project) as db:
                        existing_baseline = db.execute('SELECT id FROM baselines WHERE structure_id=? AND pseudo_set_id=? AND settings_json=? AND previous_baseline_id=?',
                            (structure.id, baseline.pseudo_set_id, baseline.settings_json, baseline.id)).fetchone()
                    point_baseline = get_baseline(project, existing_baseline[0]) if existing_baseline else commit_baseline(
                        project, structure.id, baseline.pseudo_set_id, json.loads(baseline.settings_json), previous_baseline_id=baseline.id)
                calculation = calculations.create_calculation(project, 'scf', point_baseline.id,
                    origin='study_point', overrides=point['overrides'], identifier=calculation_id)
                with project_connection(project, write=True) as db:
                    db.execute('INSERT INTO study_points VALUES (?,?,?,?,?)',
                        (identifier, ordinal, point['key'], calculation.id, canonical_json(point['values'])))
            if calculation.state != 'input_generated':
                calculations.generate_calculation(project, calculation.id, expected_revision=calculation.draft_revision)
        manifest = {'version': 1, 'study_id': identifier, 'baseline_id': baseline.id, 'specification': spec,
                    'spec_hash': study['spec_hash'], 'points': study_points(project, identifier)}
        final = study_dir(project, study)
        final.mkdir(parents=True, exist_ok=True)
        with staged_payload(project) as stage:
            (stage / 'manifest.json').write_text(canonical_json(manifest))
            (stage / 'manifest.json').replace(final / 'manifest.json')
        with project_connection(project, write=True) as db:
            db.execute("UPDATE studies SET state='generated',manifest_relpath=?,modified_at=? WHERE id=?",
                ((final / 'manifest.json').relative_to(project.root_path).as_posix(), calculations.now(), identifier))
            touch_project(db, project)
        return get_study(project, identifier)


def point_statuses(project, identifier):
    """Durable status only: missing files never imply a running job."""
    rows = []
    runs = execution.list_runs(project)
    latest = {run['calculation_id']: run for run in runs}
    for point in study_points(project, identifier):
        calculation = calculations.get_calculation(project, point['calculation_id'])
        run = latest.get(calculation.id)
        energy = json.loads(run['scientific_json']).get('scf', {}).get('total_energy_ry') if run else None
        rows.append({**point, **json.loads(point['values_json']), 'calculation': calculation.label,
                     'run_id': run['id'] if run else None, 'attempt': run['attempt'] if run else None,
                     'execution_state': run['state'] if run else 'not_run', 'total_energy_ry': energy})
    return rows


def list_results(project, identifier):
    get_study(project, identifier)
    with project_connection(project) as db:
        return [dict(row) for row in db.execute('SELECT * FROM study_results WHERE study_id=? ORDER BY revision', (identifier,))]


def get_result(project, result_id):
    with project_connection(project) as db:
        row = db.execute('SELECT * FROM study_results WHERE id=?', (result_id,)).fetchone()
    if row is None:
        raise ProjectError('The study result does not belong to this project.')
    return dict(row)


def refresh_results(project, identifier, *, run_selection=None):
    """Append a result, defaulting to the latest attempt at each point.

    An explicit ordinal-to-run mapping can select historical attempts; no failed
    retry silently falls back to an earlier successful attempt.
    """
    with _study_lock(project, identifier):
        study = get_study(project, identifier)
        if study['state'] != 'generated':
            raise ProjectError('Finish study generation before refreshing results.')
        baseline = get_baseline(project, study['baseline_id'])
        reference = load_structure(project, baseline.structure_id)
        spec = json.loads(study['spec_json'])
        statuses = point_statuses(project, identifier)
        selection = dict(run_selection or {})
        if set(selection) - {p['ordinal'] for p in statuses}:
            raise ProjectError('Run selection contains an unknown study point.')
        parsed = []; sources = []; scientific_points = []
        for point in statuses:
            run_id = selection.get(point['ordinal'], point['run_id'])
            source = {'ordinal': point['ordinal'], 'calculation_id': point['calculation_id'], 'run_id': run_id,
                      'output_relpath': None, 'sha256': None, 'state': 'not_run'}
            result = PointResult(status='missing_output')
            root = project.root_path
            if run_id:
                run = execution.get_run(project, run_id)
                if run['calculation_id'] != point['calculation_id']:
                    raise ProjectError('Selected run does not belong to this study point.')
                source['state'] = run['state']
                root = ProjectPaths(project.root_path).resolve_relative(run['work_relpath'])
                result = PointResult(status=run['state'])
                if run['state'] == 'completed':
                    matches = [a for a in execution.artifacts(project, run_id) if a['role'] == 'output' and Path(a['relpath']).name == 'pw.out']
                    try:
                        if len(matches) != 1:
                            raise ProjectError('Completed attempt has no registered PW output.')
                        artifact = matches[0]
                        source.update(output_relpath=artifact['relpath'], sha256=artifact['sha256'])
                        output = ProjectPaths(project.root_path).resolve_relative(artifact['relpath'])
                        if execution.content_hash(output) != artifact['sha256']:
                            raise ProjectError('Completed output changed after registration.')
                        result = parse_cutoff_point_output(root, atom_count=len(reference))
                        if execution.content_hash(output) != artifact['sha256']:
                            raise ProjectError('Completed output changed during parsing.')
                    except (ValueError, OSError) as exc:
                        result = PointResult(status='integrity_error', warnings=(str(exc),))
            sources.append(source)
            parsed.append(result)
            values = json.loads(point['values_json'])
            if study['kind'] == 'cutoff':
                scientific_points.append(CutoffStudyPoint(point['ordinal'], values['ecutwfc_ry'], values['ecutrho_ry'], root))
            elif study['kind'] == 'kpoint':
                scientific_points.append(KPointStudyPoint(point['ordinal'], tuple(values['k_points_grid']), values['grid_label'], root))
        parsed = attach_successive_energy_deltas(parsed)
        rows = []
        for point, result, source in zip(statuses, parsed, sources):
            rows.append({**point, **result.as_dict(), 'run_id': source['run_id'], 'execution_state': source['state'],
                         'output_path': source['output_relpath'], 'attempt': None})
            if source['run_id']:
                rows[-1]['attempt'] = execution.get_run(project, source['run_id'])['attempt']
        summary = {'schema_version': 1, 'study_id': identifier, 'study_type': study['kind'],
                   'baseline_id': baseline.id, 'reference_structure_id': baseline.structure_id,
                   'spec_hash': study['spec_hash'], 'points': rows, 'warnings': [], 'updated_at': calculations.now()}
        if study['kind'] in {'cutoff', 'kpoint'}:
            recommend = recommend_cutoffs if study['kind'] == 'cutoff' else recommend_kpoints
            recommendation = recommend(scientific_points, parsed, tolerance_mev_per_atom=spec['sweep']['tolerance_mev_per_atom'])
            summary.update(recommendation=recommendation.as_dict(), status=recommendation.status)
        else:
            successful = [r for r in rows if r['status'] == 'success']
            fitted = fit_eos([r['volume'] for r in successful], [r['total_energy_ev'] for r in successful], spec['sweep']['fit_model'])
            from ...convergence.eos_study import equilibrium_geometry_from_reference
            geometry = equilibrium_geometry_from_reference(reference, fitted.V0)
            summary['fit'] = {**asdict(fitted), 'natoms_in_fitted_cell': len(reference), 'energy_basis': 'cell_total_ev',
                'volume_basis': 'cell_total_ang3', 'equilibrium_geometry': geometry,
                'a0': (geometry.get('conventional_cell') or {}).get('a')}
            summary['status'] = 'complete' if fitted.success and len(successful) == len(rows) else 'partial_fit' if fitted.success else 'incomplete'
        versions = {'adapter': 1, 'modules': {name: execution.content_hash(Path(__file__).parents[2] / name) for name in
            ('convergence/result_parser.py','convergence/recommendation.py','convergence/eos_fit.py', 'services/studies/study_service.py')}}
        fingerprint = _hash({'spec_hash': study['spec_hash'], 'sources': sources, 'versions': versions})
        with staged_payload(project) as stage:
            result_id = str(uuid4())
            with project_connection(project) as db:
                revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM study_results WHERE study_id=?', (identifier,)).fetchone()[0]
            final = study_dir(project, study) / 'results' / f'result_{revision:03d}_{result_id[:8]}'
            final = ProjectPaths(project.root_path).resolve_relative(final.relative_to(project.root_path))
            summary.update(result_id=result_id, revision=revision, versions=versions, source_fingerprint=fingerprint)
            (stage / 'summary.json').write_text(canonical_json(summary))
            digest = execution.content_hash(stage / 'summary.json')
            final.parent.mkdir(parents=True, exist_ok=True)
            with project_connection(project, write=True) as db:
                db.execute('INSERT INTO study_results VALUES (?,?,?,?,?,?,?,?,?)', (result_id, identifier, revision,
                    canonical_json(summary), canonical_json(versions), fingerprint,
                    (final / 'summary.json').relative_to(project.root_path).as_posix(), digest, calculations.now()))
                for source in sources:
                    db.execute('INSERT INTO study_result_sources VALUES (?,?,?,?,?)',
                        (result_id, identifier, source['ordinal'], source['run_id'], canonical_json(source)))
                stage.rename(final)
                touch_project(db, project)
        return get_result(project, result_id)


def derive_equilibrium_structure(project, result_id):
    result = get_result(project, result_id)
    study = get_study(project, result['study_id'])
    summary = json.loads(result['summary_json'])
    fitted = summary.get('fit', {})
    if study['kind'] != 'eos' or not fitted.get('success') or not fitted.get('V0'):
        raise ProjectError('Select a successful EOS fit result.')
    baseline = get_baseline(project, study['baseline_id'])
    reference = load_structure(project, baseline.structure_id)
    volume = float(fitted['V0'])
    if not math.isfinite(volume) or volume <= 0:
        raise ProjectError('The fitted equilibrium volume is invalid.')
    reference.set_cell(reference.cell * (volume / reference.get_volume()) ** (1 / 3), scale_atoms=True)
    return commit_structure(project, reference, name=study['display_name'] + ' equilibrium', parent_id=baseline.structure_id,
        operation='eos_equilibrium', parameters={'result_id': result_id, 'volume_ang3': volume},
        source={'study_id': study['id'], 'result_id': result_id}, identifier=str(uuid5(UUID(result_id), 'equilibrium')))


def run_study(project, identifier, *, commands=None):
    from .. import execution_coordinator as coordinator
    study = get_study(project, identifier)
    if study['state'] != 'generated':
        raise ProjectError('Finish generating the study before running it.')
    candidates = [p['calculation_id'] for p in point_statuses(project, identifier) if p['execution_state'] != 'completed']
    if not candidates:
        raise ProjectError('All study points already have completed latest attempts.')
    return coordinator.submit_batch(project, candidates, owner_id=identifier, commands=commands)
