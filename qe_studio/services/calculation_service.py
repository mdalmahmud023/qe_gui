"""Editable calculation definitions with independent, revisioned input snapshots."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..core.project_models import Calculation, ProjectError, ProjectConflictError
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import canonical_json
from ..storage.project_repository import project_connection, record, records, staged_payload, touch_project
from .baseline_service import get_baseline, BASELINE_FIELDS
from .structure_service import load_structure, get_structure
from .pseudo_set_service import get_pseudo_set, load_pseudo_entries
from .input_generator import build_calculation_context_from_request, generate_qe_input_text
from .reproducibility import build_generation_validation_issues, build_input_reproducibility_metadata, resolve_generator_version_info

KINDS = ('scf', 'nscf', 'relax', 'vc-relax', 'bands', 'dos')
ORIGINS = ('manual', 'workflow_generated', 'study_point')
EXTRA_FIELDS = {'nbnd', 'band_path', 'band_coordinate_mode', 'dos_emin_ev', 'dos_emax_ev', 'dos_deltae_ev'}
OVERRIDE_FIELDS = BASELINE_FIELDS | EXTRA_FIELDS
_UNSET = object()


def now():
    return datetime.now(timezone.utc).isoformat()


def list_calculations(project):
    return records(project, 'calculations', Calculation)


def get_calculation(project, identifier):
    return record(project, 'calculations', identifier, Calculation)


def study_point_owner(project, identifier):
    with project_connection(project) as db:
        row = db.execute('SELECT study_id FROM study_points WHERE calculation_id=?', (identifier,)).fetchone()
    return row[0] if row else None


def calculation_dir(project, calculation):
    return ProjectPaths(project.root_path).resolve_relative('calculations/' + calculation.label)


def _snapshot(project, calculation):
    """Disposable export snapshot; readers always use SQLite."""
    root = calculation_dir(project, calculation)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ('.calculation-' + str(uuid4()) + '.json')
    temporary.write_text(canonical_json({'schema_version': 1, **asdict(calculation), 'state': calculation.state}), encoding='utf-8')
    temporary.replace(root / 'calculation.json')


def _overrides(value):
    if not isinstance(value, dict) or set(value) - OVERRIDE_FIELDS:
        raise ProjectError('Overrides contain unsupported settings. Prefix and scratch paths are managed per calculation.')
    return json.loads(canonical_json(value))


def _merged(project, baseline_id, kind, overrides):
    baseline = get_baseline(project, baseline_id)
    return {**json.loads(baseline.settings_json), **overrides, 'calculation': kind}


def create_calculation(project, kind, baseline_id, *, display_name=None, origin='manual', overrides=None,
                       duplicated_from_id=None, parent_run_id=None, identifier=None):
    if kind not in KINDS or origin not in ORIGINS:
        raise ProjectError('Unsupported calculation kind or origin.')
    overrides = _overrides(overrides or {})
    if duplicated_from_id:
        get_calculation(project, duplicated_from_id)
    if parent_run_id:
        from .execution_service import get_run
        parent = get_run(project, parent_run_id)
        parent_calc = get_calculation(project, parent['calculation_id'])
        if parent_calc.baseline_id != baseline_id:
            raise ProjectError('A child must use the parent calculation baseline.')
        inherited = {key: value for key, value in json.loads(parent_calc.resolved_request_json).items() if key in OVERRIDE_FIELDS and key not in EXTRA_FIELDS}
        overrides = {**inherited, **overrides}
    request = _merged(project, baseline_id, kind, overrides)
    # Existing records remain inspectable, but damaged UPFs cannot seed new work.
    load_pseudo_entries(project, get_baseline(project, baseline_id).pseudo_set_id)
    with project_connection(project, write=True) as db:
        sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM calculations WHERE kind=?', (kind,)).fetchone()[0]
        while ProjectPaths(project.root_path).resolve_relative(f'calculations/{kind}_{sequence:03d}').exists():
            sequence += 1
        label = f'{kind}_{sequence:03d}'
        name = (display_name or label).strip()
        if not name:
            raise ProjectError('Calculation name cannot be empty.')
        timestamp = now()
        calculation = Calculation(identifier or str(uuid4()), sequence, name, kind, origin, baseline_id, canonical_json(overrides),
                                  canonical_json(request), 1, None, None, None, '{}', '{}', None,
                                  duplicated_from_id, parent_run_id, '', timestamp, timestamp)
        db.execute('INSERT INTO calculations VALUES (' + ','.join('?' for _ in asdict(calculation)) + ')', tuple(asdict(calculation).values()))
        _snapshot(project, calculation)
        touch_project(db, project)
    return calculation


def save_draft(project, identifier, overrides, *, expected_revision, baseline_id=None, parent_run_id=_UNSET):
    current = get_calculation(project, identifier)
    if study_point_owner(project, identifier):
        raise ProjectError('Duplicate the study to change a study-point definition.')
    if current.frozen_at:
        raise ProjectError('Duplicate the frozen calculation before changing scientific settings.')
    overrides = _overrides(overrides)
    if parent_run_id is _UNSET:
        parent_run_id = current.parent_run_id
    if parent_run_id:
        from .execution_service import get_run
        get_run(project, parent_run_id)
    baseline_id = baseline_id or current.baseline_id
    # Parent binding is explicit and validated at generation; clearing it is allowed in a draft.
    request = _merged(project, baseline_id, current.kind, overrides)
    with project_connection(project, write=True) as db:
        result = db.execute('''UPDATE calculations SET baseline_id=?, overrides_json=?, resolved_request_json=?,
            parent_run_id=?, draft_revision=draft_revision+1, modified_at=? WHERE id=? AND draft_revision=? AND frozen_at IS NULL''',
            (baseline_id, canonical_json(overrides), canonical_json(request), parent_run_id, now(), identifier, expected_revision))
        if result.rowcount != 1:
            raise ProjectConflictError('Calculation changed in another tab. Reload the editor before saving.')
        touch_project(db, project)
    updated = get_calculation(project, identifier)
    _snapshot(project, updated)
    return updated


def rename_calculation(project, identifier, display_name, *, notes=None):
    if not display_name.strip():
        raise ProjectError('Calculation name cannot be empty.')
    current = get_calculation(project, identifier)
    with project_connection(project, write=True) as db:
        db.execute('UPDATE calculations SET display_name=?, notes=?, modified_at=? WHERE id=?',
                   (display_name.strip(), current.notes if notes is None else notes, now(), identifier))
        touch_project(db, project)
    updated = get_calculation(project, identifier)
    _snapshot(project, updated)
    return updated


def duplicate_calculation(project, identifier):
    original = get_calculation(project, identifier)
    return create_calculation(project, original.kind, original.baseline_id, display_name=original.display_name + ' (copy)',
        origin='manual' if original.origin == 'study_point' else original.origin, overrides=json.loads(original.overrides_json), duplicated_from_id=original.id, parent_run_id=original.parent_run_id)


def resolve_calculation(project, calculation):
    baseline = get_baseline(project, calculation.baseline_id)
    atoms = load_structure(project, baseline.structure_id)
    entries = load_pseudo_entries(project, baseline.pseudo_set_id)
    request = _merged(project, calculation.baseline_id, calculation.kind, json.loads(calculation.overrides_json))
    prefix = 'qe_' + calculation.id.replace('-', '')[:16]
    if calculation.kind in {'nscf', 'bands', 'dos'}:
        from .execution_service import validate_parent
        parent_request = validate_parent(project, calculation)
        prefix = parent_request['prefix']
    request.update(prefix=prefix, outdir='./tmp', parent_run_id=calculation.parent_run_id)
    grid = request.get('k_points_grid')
    if not isinstance(grid, (list, tuple)) or len(grid) != 3 or any(isinstance(n, bool) or not isinstance(n, (int, float)) or int(n) != n or n < 1 for n in grid):
        raise ProjectError('The k-point grid must contain three positive integers.')
    if request.get('nspin') not in (1, 2):
        raise ProjectError('Spin must be 1 or 2.')
    ctx = build_calculation_context_from_request(atoms, request, resolved_entries=entries)
    ctx.validate()
    issues = build_generation_validation_issues(atoms, ctx, requested_workflow=calculation.kind)
    errors = [issue.message for issue in issues if issue.level == 'error']
    if errors:
        raise ProjectError(' '.join(errors))
    return baseline, atoms, entries, request, ctx, issues


def definition_hash(project, baseline, request):
    structure = get_structure(project, baseline.structure_id)
    pseudo = get_pseudo_set(project, baseline.pseudo_set_id)
    scientific_request = {key: value for key, value in request.items() if key not in {'prefix', 'outdir'}}
    return hashlib.sha256(canonical_json({'version': 1, 'structure_hash': structure.structure_hash,
        'pseudo_set_hash': pseudo.pseudo_set_hash, 'request': scientific_request}).encode()).hexdigest()


def generate_calculation(project, identifier, *, expected_revision):
    calculation = get_calculation(project, identifier)
    if calculation.frozen_at:
        raise ProjectError('A calculation with runs cannot regenerate inputs. Duplicate it first.')
    if calculation.draft_revision != expected_revision:
        raise ProjectConflictError('Calculation changed in another tab. Reload before generating.')
    baseline, atoms, entries, request, ctx, issues = resolve_calculation(project, calculation)
    final = ProjectPaths(project.root_path).resolve_relative(
        f'calculations/{calculation.label}/input/revision_{expected_revision:03d}_{uuid4().hex[:8]}')
    with staged_payload(project) as stage:
        payload = stage / 'input'; payload.mkdir()
        if calculation.kind == 'dos':
            from .postprocessing import build_dos_input_text
            emin = float(request.get('dos_emin_ev', -20)); emax = float(request.get('dos_emax_ev', 20)); delta = float(request.get('dos_deltae_ev', .1))
            if not emin < emax or delta <= 0:
                raise ProjectError('DOS energy range and spacing are invalid.')
            text = build_dos_input_text(prefix=request['prefix'], outdir='./tmp', fildos='dos.dat', emin_ev=emin, emax_ev=emax,
                                       deltae_ev=delta, bz_sum='smearing', ngauss=0, degauss_ry=float(request.get('degauss', .01)))
            (payload / 'dos.in').write_text(text)
            steps = [{'kind': 'dos', 'executable': 'dos.x', 'input': 'dos.in', 'output': 'dos.out'}]
        else:
            # Apply the same magnetic moment mapping as the existing workflow adapter.
            if ctx.physical.nspin == 2:
                atoms = atoms.copy()
                atoms.set_initial_magnetic_moments([ctx.physical.starting_magnetization.get(atom.symbol, 0) for atom in atoms])
            text, _, _ = generate_qe_input_text(atoms, ctx, {s: e['filename'] for s, e in entries.items()},
                Path(next(iter(entries.values()))['local_path']).parent, run_dir=payload, execution_dir=final)
            if calculation.kind in {'nscf', 'bands'}:
                from .postprocessing import _set_namelist_value
                nbnd = request.get('nbnd')
                if nbnd is not None:
                    if isinstance(nbnd, bool) or int(nbnd) != nbnd or nbnd < 1:
                        raise ProjectError('Band count must be a positive integer.')
                    text = _set_namelist_value(text, 'SYSTEM', 'nbnd', str(int(nbnd)))
            if calculation.kind == 'bands':
                from .postprocessing import resolve_qe_run_context, transform_input_to_bands_pw, build_bands_pp_input_text
                from .structure_tools import generate_band_path_rows
                if not request.get('nbnd'):
                    raise ProjectError('Specify nbnd for a bands calculation.')
                path = request.get('band_path')
                if not path:
                    path = generate_band_path_rows(atoms).rows_text
                text = transform_input_to_bands_pw(resolve_qe_run_context(payload), nbnd=int(request['nbnd']),
                    coordinate_mode=request.get('band_coordinate_mode', 'crystal_b'), band_path_text=path)
                (payload / 'bands_pp.in').write_text(build_bands_pp_input_text(prefix=request['prefix'], outdir='./tmp'))
            (payload / 'pw.in').write_text(text)
            (payload / 'espresso.pwi').write_text(text)
            steps = [{'kind': calculation.kind, 'executable': 'pw.x', 'input': 'pw.in', 'output': 'pw.out'}]
            if calculation.kind == 'bands':
                steps.append({'kind': 'bands_pp', 'executable': 'bands.x', 'input': 'bands_pp.in', 'output': 'bands_pp.out'})
        generator = {**resolve_generator_version_info(), 'calculation_adapter_version': 1}
        package = Path(__file__).parents[1]
        source_hashes = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in sorted(package.rglob('*.py'))}
        generator['source_sha256'] = hashlib.sha256(canonical_json(source_hashes).encode()).hexdigest()
        metadata = build_input_reproducibility_metadata(atoms, ctx, requested_workflow=calculation.kind,
            pseudo_settings=json.loads(get_pseudo_set(project, baseline.pseudo_set_id).settings_json),
            source_metadata={}, structure_signature=None, structure_mode='committed', validation_issues=issues,
            generator_info=generator, run_dir=final)
        fingerprint = definition_hash(project, baseline, request)
        metadata.update(project_id=project.id, calculation_id=identifier, baseline_id=baseline.id,
            structure_id=baseline.structure_id, pseudo_set_id=baseline.pseudo_set_id, resolved_request=request,
            draft_revision=expected_revision, definition_hash=fingerprint, parent_run_id=calculation.parent_run_id, steps=steps)
        (payload / 'metadata.json').write_text(canonical_json(metadata))
        manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in payload.iterdir()}
        with project_connection(project, write=True) as db:
            result = db.execute('''UPDATE calculations SET generated_revision=?, resolved_request_json=?, definition_hash=?,
                input_relpath=?, input_manifest_json=?, generator_json=?, modified_at=?
                WHERE id=? AND draft_revision=? AND frozen_at IS NULL''',
                (expected_revision, canonical_json(request), fingerprint, final.relative_to(project.root_path).as_posix(),
                 canonical_json(manifest), canonical_json(generator), now(), identifier, expected_revision))
            if result.rowcount != 1:
                raise ProjectConflictError('Calculation changed during generation. Reload and regenerate the current draft.')
            final.parent.mkdir(parents=True, exist_ok=True)
            payload.rename(final)
            touch_project(db, project)
    updated = get_calculation(project, identifier)
    _snapshot(project, updated)
    return updated


def generated_inputs(project, calculation):
    if not calculation.input_relpath:
        raise ProjectError('Generate input before continuing.')
    root = ProjectPaths(project.root_path).resolve_relative(calculation.input_relpath)
    manifest = json.loads(calculation.input_manifest_json)
    files = {}
    for filename, expected in manifest.items():
        path = ProjectPaths(project.root_path).resolve_relative(str(Path(calculation.input_relpath) / filename))
        if path.parent != root or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ProjectError('Generated input integrity check failed; a file is missing or changed.')
        files[filename] = path
    return files
