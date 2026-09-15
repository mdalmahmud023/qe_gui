"""Immutable common scientific configuration, upstream of future calculations."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from uuid import uuid4

from ..core.project_models import BaselineRevision, ProjectError
from ..core.structure_identity import canonical_json
from ..storage.project_repository import project_connection, record, records, touch_project
from .baseline_defaults import default_qe_parameters
from .input_generator import build_calculation_context_from_request
from .project_service import open_project
from .pseudo_set_service import load_pseudo_entries
from .structure_service import load_structure
from .reproducibility import build_generation_validation_issues

NON_BASELINE_FIELDS = {'calculation', 'prefix', 'prefix_source', 'outdir', 'outdir_source'}
BASELINE_FIELDS = set(default_qe_parameters()) - NON_BASELINE_FIELDS


def list_baselines(project):
    return records(project, 'baselines', BaselineRevision)


def get_baseline(project, identifier):
    return record(project, 'baselines', identifier, BaselineRevision)


def default_baseline_settings(atoms, resolved_entries=None):
    return {key: value for key, value in default_qe_parameters(resolved_entries, atoms).items() if key in BASELINE_FIELDS}


def resolve_baseline_settings(project, structure_id, pseudo_set_id, settings):
    if not isinstance(settings, dict) or set(settings) - BASELINE_FIELDS:
        raise ProjectError('Baseline settings must contain only supported physical and numerical defaults.')
    atoms = load_structure(project, structure_id)
    entries = load_pseudo_entries(project, pseudo_set_id)
    if set(atoms.get_chemical_symbols()) != set(entries):
        raise ProjectError('The pseudopotential set must match the structure species exactly.')
    request = {**default_baseline_settings(atoms, entries), **settings}
    canonical_json(request)  # Reject non-finite or unsupported JSON before coercion.
    if request.get('nspin') not in (1, 2):
        raise ProjectError('Spin must be nspin=1 or nspin=2.')
    grid = request.get('k_points_grid')
    if not isinstance(grid, (list, tuple)) or len(grid) != 3 or any(isinstance(n, bool) or int(n) != n or n < 1 for n in grid):
        raise ProjectError('The k-point grid must contain three positive integers.')
    ctx = build_calculation_context_from_request(atoms, {**request, 'calculation': 'scf'}, resolved_entries=entries)
    ctx.validate()
    issues = build_generation_validation_issues(atoms, ctx, requested_workflow='scf')
    errors = [issue.message for issue in issues if issue.level == 'error']
    if errors:
        raise ProjectError(' '.join(errors))
    rendered = {'version': 1, 'physical': asdict(ctx.physical), 'numerical': asdict(ctx.numerical),
                'hubbard_u': ctx.hubbard_u.to_metadata(species_order=ctx.species_order),
                'vdw': ctx.vdw.normalized_method.value,
                'features': {key: asdict(value) for key, value in ctx.feature_states.items() if key in {'spin', 'soc', 'dftu', 'vdw'}},
                'validation': [asdict(issue) for issue in issues]}
    return request, rendered, ctx


def commit_baseline(project, structure_id, pseudo_set_id, settings, *, previous_baseline_id=None, _recommendation=None):
    request, rendered, _ = resolve_baseline_settings(project, structure_id, pseudo_set_id, settings)
    if previous_baseline_id:
        get_baseline(project, previous_baseline_id)
    with project_connection(project, write=True) as db:
        sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM baselines').fetchone()[0]
        entry = BaselineRevision(str(uuid4()), sequence, structure_id, pseudo_set_id, previous_baseline_id,
                                 canonical_json(request), canonical_json(rendered), datetime.now(timezone.utc).isoformat())
        db.execute('INSERT INTO baselines VALUES (?,?,?,?,?,?,?,?)', tuple(entry.__dict__.values()))
        if _recommendation:
            result_id, fields, policy_version = _recommendation
            db.execute('INSERT INTO baseline_recommendations VALUES (?,?,?,?,?)',
                (entry.id, result_id, canonical_json(fields), policy_version, entry.created_at))
        touch_project(db, project)
    return entry


def baseline_calculation_context(project, identifier):
    entry = get_baseline(project, identifier)
    return resolve_baseline_settings(project, entry.structure_id, entry.pseudo_set_id, json.loads(entry.settings_json))[2]


def set_active_baseline(project, identifier):
    entry = get_baseline(project, identifier)
    baseline_calculation_context(project, identifier)  # Verify owned bytes before adoption.
    with project_connection(project, write=True) as db:
        touch_project(db, project, expected=True, active_structure_id=entry.structure_id, active_baseline_id=identifier)
    return open_project(project.root_path)
