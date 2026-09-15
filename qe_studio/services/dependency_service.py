"""Typed provenance and on-demand scientific compatibility, policy version 1.

Existing parent runs, study membership, consumed runs, and structure lineage stay
authoritative in their original tables. This service never changes run status.
Only recommendation-to-baseline provenance requires a new persisted relation.
"""
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json

from ..core.project_models import ProjectError
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import canonical_json
from ..storage.project_repository import project_connection
from . import calculation_service as calculations, execution_service as execution
from .baseline_service import BASELINE_FIELDS, get_baseline, commit_baseline
from .structure_service import get_structure
from .pseudo_set_service import get_pseudo_set
from .project_service import open_project
from .studies import study_service as studies

FRESHNESS_POLICY_VERSION = 1


class Freshness(str, Enum):
    CURRENT = 'current'
    STALE = 'stale'
    UNKNOWN = 'unknown'


class Relationship(str, Enum):
    PARENT_RUN = 'parent_run'
    CONSUMES_ARTIFACT = 'consumes_artifact'
    CONSUMED_RUN = 'consumed_run'
    DERIVED_FROM = 'derived_from'
    RECOMMENDED_BY = 'recommended_by'


@dataclass(frozen=True)
class Reason:
    code: str
    message: str
    field: str | None = None
    before: object = None
    after: object = None
    upstream_id: str | None = None


@dataclass(frozen=True)
class Dependency:
    relationship: Relationship
    upstream_kind: str
    upstream_id: str
    downstream_kind: str
    downstream_id: str
    fingerprint: str | None = None
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileCheck:
    role: str
    relpath: str | None
    availability: str
    integrity: str
    expected: str | None = None
    found: str | None = None
    message: str = ''


@dataclass
class Assessment:
    target_kind: str
    target_id: str
    baseline_id: str | None
    state: Freshness = Freshness.UNKNOWN
    reasons: list[Reason] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    files: list[FileCheck] = field(default_factory=list)
    capabilities: dict[str, bool | None] = field(default_factory=dict)
    policy_version: int = FRESHNESS_POLICY_VERSION
    evaluation_key: str = ''


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _scientific_settings(settings):
    normalized = {k: v for k, v in settings.items() if k in BASELINE_FIELDS and k != 'spin_source'}
    for name in ('starting_magnetization', 'hubbard_u'):
        value = normalized.get(name)
        if isinstance(value, dict):
            normalized[name] = {species: {k: v for k, v in entry.items() if k not in {'source','species'}}
                                if isinstance(entry, dict) else {'value': entry} for species, entry in value.items()}
    if settings.get('nspin') == 1:
        normalized.pop('starting_magnetization', None)
    if settings.get('dftu_enabled') is False:
        normalized['hubbard_u'] = {}
    if settings.get('occupations') != 'smearing':
        normalized.pop('smearing', None)
        normalized.pop('degauss', None)
    return normalized


LABELS = {'ecutwfc':'Wavefunction cutoff (Ry)', 'ecutrho':'Charge density cutoff (Ry)', 'k_points_grid':'K-point grid',
          'nspin':'Spin', 'noncolin':'Noncollinear spin', 'lspinorb':'SOC', 'dftu_enabled':'Hubbard U enabled',
          'hubbard_u':'Hubbard U (eV)', 'vdw_method':'vdW method', 'starting_magnetization':'Starting magnetization',
          'occupations':'Occupations', 'smearing':'Smearing method', 'degauss':'Smearing width (Ry)',
          'conv_thr':'SCF convergence threshold (Ry)', 'mixing_beta':'Mixing beta'}


def compare_snapshots(old, new, *, ignore=(), structure_only=False):
    """Pure projection comparison; missing evidence is never guessed from defaults."""
    reasons = []
    if not old or not new:
        return Freshness.UNKNOWN, [Reason('missing_context', 'A scientific baseline or its provenance is unavailable.')]
    for key, label in [('structure_hash','Structure'), *([] if structure_only else [('pseudo_set_hash','Pseudopotential set')])]:
        if not old.get(key) or not new.get(key):
            reasons.append(Reason('missing_provenance', f'{label} fingerprint is unavailable.', key))
        elif old[key] != new[key]:
            label_key = 'structure_label' if key == 'structure_hash' else 'pseudo_set_label'
            detail = f" {old[label_key]} → {new[label_key]}." if old.get(label_key) and new.get(label_key) else '.'
            reasons.append(Reason('changed', f'{label} changed' + detail, key, old[key], new[key]))
            if key == 'pseudo_set_hash':
                a, b = old.get('species_hashes', {}), new.get('species_hashes', {})
                for species in sorted(a.keys() | b.keys()):
                    if a.get(species) != b.get(species):
                        reasons.append(Reason('changed', f'{species} pseudopotential changed.', 'pseudo:' + species, a.get(species), b.get(species)))
    if not structure_only:
        a, b = old.get('settings'), new.get('settings')
        required = BASELINE_FIELDS - {'spin_source'} - set(ignore)
        if not isinstance(a, dict) or not isinstance(b, dict) or required - a.keys() or required - b.keys():
            reasons.append(Reason('missing_provenance', 'The scientific settings snapshot is incomplete.'))
        if isinstance(a, dict) and isinstance(b, dict):
            evidenced = a.keys() & b.keys()
            a, b = _scientific_settings(a), _scientific_settings(b)
            for key in sorted(((a.keys() | b.keys()) & evidenced) - set(ignore)):
                # Conditional settings absent in one projection matter when enabled in the other.
                if a.get(key) != b.get(key):
                    before, after = a.get(key), b.get(key)
                    reasons.append(Reason('changed', f'{LABELS.get(key, key)} changed from {before} → {after}.', key, before, after))
    state = Freshness.STALE if any(r.code == 'changed' for r in reasons) else Freshness.UNKNOWN if reasons else Freshness.CURRENT
    return state, reasons


def baseline_snapshot(project, identifier):
    baseline = get_baseline(project, identifier)
    structure = get_structure(project, baseline.structure_id)
    pseudo = get_pseudo_set(project, baseline.pseudo_set_id)
    with project_connection(project) as db:
        species = {r['species']: r['sha256'] for r in db.execute('SELECT species,sha256 FROM pseudo_entries WHERE pseudo_set_id=?', (pseudo.id,))}
    return {'structure_hash': structure.structure_hash if structure.hash_version == 1 else None,
            'pseudo_set_hash': pseudo.pseudo_set_hash, 'species_hashes': species, 'settings': json.loads(baseline.settings_json),
            'structure_id': structure.id, 'pseudo_set_id': pseudo.id,
            'structure_label': f'structure_{structure.sequence:03d}', 'pseudo_set_label': f'set_{pseudo.sequence:03d}'}


def dependencies(project, kind, identifier):
    """Read known typed relations, without copying parent/membership/source tables."""
    edges = []
    def add(relation, upstream_kind, upstream_id, fingerprint=None, fields=()):
        edges.append(Dependency(Relationship(relation), upstream_kind, upstream_id, kind, identifier, fingerprint, tuple(fields)))
    if kind == 'calculation':
        calculation = calculations.get_calculation(project, identifier)
        add('derived_from', 'baseline', calculation.baseline_id)
        if calculation.parent_run_id:
            run = execution.get_run(project, calculation.parent_run_id)
            add('parent_run', 'run', run['id'])
            for artifact in execution.artifacts(project, run['id']):
                if artifact['role'] == 'restart':
                    add('consumes_artifact', 'artifact', artifact['id'], artifact['sha256'])
    elif kind == 'run':
        run = execution.get_run(project, identifier)
        add('derived_from', 'calculation', run['calculation_id'])
    elif kind == 'study':
        study = studies.get_study(project, identifier)
        add('derived_from', 'baseline', study['baseline_id'])
    elif kind == 'study_result':
        result = studies.get_result(project, identifier)
        add('derived_from', 'study', result['study_id'], result['source_fingerprint'])
        with project_connection(project) as db:
            sources = list(db.execute('SELECT * FROM study_result_sources WHERE result_id=?', (identifier,)))
        for row in sources:
            source = json.loads(row['source_json'])
            if row['run_id']:
                add('consumed_run', 'run', row['run_id'], source.get('sha256'))
                for artifact in execution.artifacts(project, row['run_id']):
                    if artifact['relpath'] == source.get('output_relpath'):
                        add('consumes_artifact', 'artifact', artifact['id'], source.get('sha256'))
    elif kind == 'structure':
        structure = get_structure(project, identifier)
        if structure.parent_structure_id:
            add('derived_from', 'structure', structure.parent_structure_id)
        source = json.loads(structure.source_json)
        if source.get('result_id') and structure.operation == 'eos_equilibrium':
            result = studies.get_result(project, source['result_id'])
            add('derived_from', 'study_result', result['id'], result['source_fingerprint'])
    elif kind == 'baseline':
        baseline = get_baseline(project, identifier)
        add('derived_from', 'structure', baseline.structure_id)
        add('derived_from', 'pseudo_set', baseline.pseudo_set_id)
        with project_connection(project) as db:
            for row in db.execute('SELECT * FROM baseline_recommendations WHERE baseline_id=?', (identifier,)):
                add('recommended_by', 'study_result', row['study_result_id'], fields=json.loads(row['fields_json']))
    else:
        raise ProjectError('Unsupported provenance target.')
    return edges


def _check_file(project, role, relpath, expected):
    if not relpath:
        return FileCheck(role, None, 'missing', 'unknown', expected, message='No registered artifact is available.')
    try:
        path = ProjectPaths(project.root_path).resolve_relative(relpath)
        if not path.exists():
            return FileCheck(role, relpath, 'missing', 'unknown', expected, message='Payload is missing; historical metadata is retained.')
        found = execution.content_hash(path)
        integrity = 'verified' if expected and found == expected else 'corrupt' if expected else 'unknown'
        return FileCheck(role, relpath, 'available', integrity, expected, found,
                         'Integrity check failed.' if integrity == 'corrupt' else '')
    except OSError as exc:
        return FileCheck(role, relpath, 'unreadable', 'unknown', expected, message=str(exc))
    except ValueError as exc:
        return FileCheck(role, relpath, 'unavailable', 'corrupt', expected, message=str(exc))


def audit_baseline(project, identifier):
    baseline = get_baseline(project, identifier)
    structure = get_structure(project, baseline.structure_id)
    pseudo = get_pseudo_set(project, baseline.pseudo_set_id)
    checks = [_check_file(project, 'structure snapshot', structure.snapshot_relpath, structure.snapshot_sha256),
              _check_file(project, 'pseudopotential manifest', pseudo.manifest_relpath, pseudo.manifest_sha256)]
    with project_connection(project) as db:
        rows = list(db.execute('SELECT * FROM pseudo_entries WHERE pseudo_set_id=?', (pseudo.id,)))
    checks.extend(_check_file(project, row['species'] + ' pseudopotential', row['relpath'], row['sha256']) for row in rows)
    return checks


def audit_run(project, identifier):
    run = execution.get_run(project, identifier)
    files = [_check_file(project, artifact['role'], artifact['relpath'], artifact['sha256']) for artifact in execution.artifacts(project, identifier)]
    if not any(f.role == 'restart' for f in files):
        files.append(_check_file(project, 'restart', None, None))
    return files


def _study_ignore(kind):
    return {'ecutwfc','ecutrho'} if kind == 'cutoff' else {'k_points_grid'} if kind == 'kpoint' else set()


def evaluate(project, kind, identifier, *, baseline_id=None, check_files=True):
    """Evaluate a selected target and its known upstream chain, never all projects.

    Scientific comparisons use committed fingerprints even when payloads are
    missing/corrupt. File checks and capabilities are independent of freshness.
    """
    current = open_project(project.root_path)
    if current.id != project.id:
        raise ProjectError('Project identity changed.')
    comparison_id = baseline_id or current.active_baseline_id
    comparison = baseline_snapshot(project, comparison_id) if comparison_id else None
    memo = {}; visiting = set(); audited_baselines = {}

    def assess(target_kind, target_id):
        key = (target_kind, target_id)
        if key in memo:
            return memo[key]
        report = Assessment(target_kind, target_id, comparison_id)
        if key in visiting:
            report.reasons = [Reason('missing_provenance', 'A cyclic upstream relationship prevents compatibility evaluation.')]
            return report
        visiting.add(key)
        report.dependencies = dependencies(project, target_kind, target_id)
        reference_id = None; snapshot = None; ignored = set(); target_hash = None; upstream = []
        if target_kind == 'calculation':
            calculation = calculations.get_calculation(project, target_id)
            reference_id = calculation.baseline_id
            snapshot = baseline_snapshot(project, reference_id)
            snapshot['settings'] = json.loads(calculation.resolved_request_json)
            target_hash = _hash({'definition': calculation.definition_hash, 'revision': calculation.draft_revision, 'request': snapshot['settings']})
            owner = calculations.study_point_owner(project, target_id)
            if owner:
                study = studies.get_study(project, owner)
                reference_id = study['baseline_id']
                snapshot = baseline_snapshot(project, reference_id)
                ignored = _study_ignore(study['kind'])
            elif calculation.kind in {'nscf','bands','dos'}:
                ignored = {'k_points_grid','occupations','smearing','degauss','conv_thr','mixing_beta'}
            if calculation.parent_run_id:
                parent = execution.get_run(project, calculation.parent_run_id)
                upstream.append(assess('calculation', parent['calculation_id']))
                artifacts = [a for a in execution.artifacts(project, parent['id']) if a['role'] == 'restart']
                if check_files:
                    checks = [_check_file(project, 'parent restart', a['relpath'], a['sha256']) for a in artifacts] or [_check_file(project,'parent restart',None,None)]
                    report.files.extend(checks)
                    report.capabilities['parent_restart_ready'] = parent['state'] == 'completed' and all(c.availability == 'available' and c.integrity == 'verified' for c in checks)
            elif calculation.kind in {'nscf','bands','dos'}:
                report.reasons.append(Reason('missing_provenance', 'An explicit parent run has not been selected.'))
            if check_files and calculation.input_relpath:
                for filename, digest in json.loads(calculation.input_manifest_json).items():
                    report.files.append(_check_file(project, 'generated input', calculation.input_relpath + '/' + filename, digest))
        elif target_kind == 'run':
            run = execution.get_run(project, target_id)
            upstream.append(assess('calculation', run['calculation_id']))
            target_hash = _hash(json.loads(run['frozen_definition_json']))
            if check_files:
                report.files.extend(audit_run(project, target_id))
                report.capabilities['restart_available'] = any(f.role == 'restart' and f.availability == 'available' and f.integrity == 'verified' for f in report.files)
        elif target_kind == 'study':
            study = studies.get_study(project, target_id)
            reference_id = study['baseline_id']; ignored = _study_ignore(study['kind'])
            snapshot = baseline_snapshot(project, reference_id); target_hash = study['spec_hash']
        elif target_kind == 'study_result':
            result = studies.get_result(project, target_id)
            upstream.append(assess('study', result['study_id']))
            target_hash = result['source_fingerprint']
            if check_files:
                report.files.append(_check_file(project, 'study result', result['payload_relpath'], result['payload_sha256']))
            with project_connection(project) as db:
                sources = list(db.execute('SELECT * FROM study_result_sources WHERE result_id=?', (target_id,)))
            if not sources:
                report.reasons.append(Reason('missing_provenance', 'Exact point-run provenance is unavailable.'))
            for source in sources:
                if check_files and source['run_id']:
                    evidence = json.loads(source['source_json'])
                    if evidence.get('output_relpath'):
                        report.files.append(_check_file(project, 'consumed output', evidence['output_relpath'], evidence.get('sha256')))
        elif target_kind == 'baseline':
            reference_id = target_id; snapshot = baseline_snapshot(project, target_id); target_hash = _hash(snapshot)
        elif target_kind == 'structure':
            structure = get_structure(project, target_id)
            snapshot = {'structure_hash': structure.structure_hash if structure.hash_version == 1 else None}
            target_hash = structure.structure_hash
            if check_files:
                report.files.append(_check_file(project,'structure snapshot',structure.snapshot_relpath,structure.snapshot_sha256))
        state, reasons = compare_snapshots(snapshot, comparison, ignore=ignored, structure_only=target_kind == 'structure') if snapshot is not None else (Freshness.CURRENT, [])
        report.reasons.extend(reasons)
        for parent in upstream:
            if parent.state != Freshness.CURRENT:
                report.reasons.extend(Reason(r.code, 'Upstream: ' + r.message, r.field, r.before, r.after, parent.target_id) for r in parent.reasons)
            report.files.extend(parent.files)
        if not comparison:
            report.reasons.append(Reason('missing_context', 'Select an active baseline to evaluate compatibility.'))
        report.state = Freshness.STALE if any(r.code == 'changed' for r in report.reasons) else Freshness.UNKNOWN if report.reasons else state
        if check_files:
            for base in {reference_id, comparison_id} - {None}:
                if base not in audited_baselines:
                    audited_baselines[base] = audit_baseline(project, base)
                report.files.extend(audited_baselines[base])
        report.files = list(dict.fromkeys(report.files))
        report.evaluation_key = _hash({'policy_version': FRESHNESS_POLICY_VERSION, 'target': key, 'baseline': comparison_id,
            'definition': target_hash, 'comparison': comparison, 'dependencies': [p.evaluation_key for p in upstream],
            'artifacts': [(f.relpath,f.expected,f.found,f.availability) for f in report.files]})
        visiting.remove(key); memo[key] = report
        return report
    return assess(kind, identifier)


def apply_recommendation(project, result_id, *, baseline_id=None):
    """Create, without adopting, a baseline with an atomic provenance record."""
    result = studies.get_result(project, result_id)
    study = studies.get_study(project, result['study_id'])
    selected = baseline_id or open_project(project.root_path).active_baseline_id
    if not selected:
        raise ProjectError('Select a baseline before applying a recommendation.')
    report = evaluate(project, 'study_result', result_id, baseline_id=selected)
    if report.state != Freshness.CURRENT:
        raise ProjectError('The study is not compatible with the selected baseline: ' + '; '.join(r.message for r in report.reasons))
    if any(f.integrity == 'corrupt' for f in report.files):
        raise ProjectError('Resolve the integrity problem before applying this recommendation.')
    summary = json.loads(result['summary_json'])
    if summary.get('status') != 'complete':
        raise ProjectError('A complete successful study result is required to apply a recommendation.')
    baseline = get_baseline(project, selected)
    settings = json.loads(baseline.settings_json); structure_id = baseline.structure_id
    recommendation = summary.get('recommendation', {})
    if study['kind'] == 'cutoff':
        settings.update(ecutwfc=recommendation['recommended_ecutwfc_ry'], ecutrho=recommendation['recommended_ecutrho_ry'])
        fields = ['ecutwfc','ecutrho']
    elif study['kind'] == 'kpoint':
        settings['k_points_grid'] = recommendation['recommended_k_grid']; fields = ['k_points_grid']
    else:
        structure_id = studies.derive_equilibrium_structure(project, result_id).id; fields = ['structure_id']
    return commit_baseline(project, structure_id, baseline.pseudo_set_id, settings, previous_baseline_id=selected,
                           _recommendation=(result_id, fields, FRESHNESS_POLICY_VERSION))


def baseline_provenance(project, identifier):
    """Field origins inherited through unchanged baseline values, for display."""
    baseline = get_baseline(project, identifier)
    values = {**json.loads(baseline.settings_json), 'structure_id': baseline.structure_id}
    eligible = set(values); found = []; seen = set()
    while baseline and baseline.id not in seen:
        seen.add(baseline.id)
        current = {**json.loads(baseline.settings_json), 'structure_id': baseline.structure_id}
        eligible &= {k for k in eligible if current.get(k) == values[k]}
        for link in dependencies(project, 'baseline', baseline.id):
            if link.relationship == Relationship.RECOMMENDED_BY:
                fields = tuple(f for f in link.fields if f in eligible)
                if fields:
                    found.append(Dependency(link.relationship, link.upstream_kind, link.upstream_id, 'baseline', identifier, fields=fields))
                    eligible -= set(fields)
        baseline = get_baseline(project, baseline.previous_baseline_id) if baseline.previous_baseline_id and eligible else None
    found.extend(dependencies(project, 'structure', values['structure_id']))
    return found


def duplicate_using_baseline(project, calculation_id, *, baseline_id=None):
    original = calculations.get_calculation(project, calculation_id)
    selected = baseline_id or open_project(project.root_path).active_baseline_id
    if not selected:
        raise ProjectError('Select an active baseline first.')
    # Baseline-controlled physics and SCF numerics follow the new context. Preserve
    # workflow-specific grids/band/DOS controls, and require explicit parent rebinding.
    allowed = calculations.EXTRA_FIELDS | ({'k_points_grid','occupations','smearing','degauss'} if original.kind in {'nscf','bands','dos'} else set())
    overrides = {k: v for k, v in json.loads(original.overrides_json).items() if k in allowed}
    return calculations.create_calculation(project, original.kind, selected, display_name=original.display_name + ' (current baseline)',
        origin='manual', overrides=overrides, duplicated_from_id=original.id, parent_run_id=None)
