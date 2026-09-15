import json
from pathlib import Path
import sqlite3
import sys
import time

from ase.build import bulk
import pytest

from test_calculations import configured_project
from test_project_execution import isolate_runtime, SCF_SCRIPT, finish
from qe_studio.core.project_models import ProjectError, ProjectConflictError
from qe_studio.services.studies import study_service as studies
from qe_studio.services import calculation_service as calculations, execution_service as execution, execution_coordinator as coordinator
from qe_studio.services.structure_service import load_structure, commit_structure, set_active_structure
from qe_studio.services.baseline_service import get_baseline
from qe_studio.services.project_service import open_project, create_project
from qe_studio.storage.project_repository import project_connection
from qe_studio.services.plotting.study_plots import plot_cutoff_convergence, plot_kpoint_convergence, plot_eos_study


def generated(project, baseline, kind='cutoff', spec=None):
    study = studies.create_study(project, kind, baseline.id, spec or studies.DEFAULT_SPECS[kind])
    return studies.generate_study(project, study['id'], expected_revision=1)


def finish_batch(project, study):
    batch = coordinator.BATCHES[(project.id, study['id'])]
    batch['thread'].join(timeout=25)
    assert not batch['thread'].is_alive(), 'Batch failed to release execution ownership'
    assert batch['error'] is None, batch['error']
    return studies.point_statuses(project, study['id'])


def test_cutoff_points_inherit_only_swept_overrides_and_freeze_spec(configured_project):
    p, b = configured_project
    study = generated(p, b)
    points = studies.study_points(p, study['id'])
    assert len(points) == 5
    for point in points:
        calc = calculations.get_calculation(p, point['calculation_id'])
        assert calc.origin == 'study_point' and calc.kind == 'scf' and calc.baseline_id == b.id
        assert set(json.loads(calc.overrides_json)) <= {'ecutwfc','ecutrho'}
        assert calc.frozen_at is None and calc.state == 'input_generated'
        with pytest.raises(ProjectError, match='study'):
            calculations.save_draft(p, calc.id, {'ecutwfc': 100}, expected_revision=1)
    with pytest.raises(ProjectError, match='Duplicate'):
        studies.save_draft(p, study['id'], {'ecutwfc_values_ry':[40,50,60]}, expected_revision=1)
    with pytest.raises(sqlite3.IntegrityError), project_connection(p, write=True) as db:
        db.execute("UPDATE studies SET spec_json='{}' WHERE id=?", (study['id'],))
    duplicate = studies.duplicate_study(p, study['id'])
    assert duplicate['id'] != study['id'] and duplicate['state'] == 'draft'
    assert studies.study_points(p, duplicate['id']) == []
    assert studies.generate_study(p, study['id'], expected_revision=1) == study
    assert len(studies.study_points(p, study['id'])) == 5
    independent = calculations.duplicate_calculation(p, points[0]['calculation_id'])
    assert independent.origin == 'manual'
    calculations.save_draft(p, independent.id, {'ecutwfc':100}, expected_revision=1)


def test_draft_conflict_and_resumable_generation(configured_project, monkeypatch):
    p,b = configured_project
    study = studies.create_study(p, 'cutoff', b.id, {'ecutwfc_values_ry':[40,50,60]})
    studies.save_draft(p, study['id'], {'ecutwfc_values_ry':[40,55,70]}, expected_revision=1)
    with pytest.raises(ProjectConflictError):
        studies.save_draft(p, study['id'], {}, expected_revision=1)
    generate = calculations.generate_calculation
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 2:
            raise RuntimeError('interrupted generation')
        return generate(*args, **kwargs)
    monkeypatch.setattr(calculations, 'generate_calculation', fail_second)
    with pytest.raises(RuntimeError, match='interrupted'):
        studies.generate_study(p, study['id'], expected_revision=2)
    original = studies.study_points(p, study['id'])
    assert len(original) == 2 and studies.get_study(p, study['id'])['state'] == 'generating'
    with pytest.raises(ProjectError):
        execution.create_run(p, original[0]['calculation_id'], expected_revision=1)
    monkeypatch.setattr(calculations, 'generate_calculation', generate)
    studies.generate_study(p, study['id'], expected_revision=2)
    assert studies.study_points(p, study['id'])[:2] == original
    assert len(calculations.list_calculations(p)) == 3


def test_generation_recovers_calculation_committed_before_membership(configured_project, monkeypatch):
    p,b=configured_project
    study=studies.create_study(p,'eos',b.id,studies.DEFAULT_SPECS['eos'])
    create=calculations.create_calculation
    def interrupted(*args,**kwargs):
        create(*args,**kwargs)
        raise RuntimeError('crash after calculation commit')
    monkeypatch.setattr(calculations,'create_calculation',interrupted)
    with pytest.raises(RuntimeError):studies.generate_study(p,study['id'],expected_revision=1)
    assert len(calculations.list_calculations(p))==1
    assert studies.study_points(p,study['id'])==[]
    monkeypatch.setattr(calculations,'create_calculation',create)
    studies.generate_study(p,study['id'],expected_revision=1)
    assert len(calculations.list_calculations(p))==5
    with project_connection(p) as db:
        assert db.execute('SELECT count(*) FROM baselines').fetchone()[0]==6


def test_effective_grids_deduplicate_and_eos_rejects_insufficient_or_duplicate_scales(configured_project):
    p,b = configured_project
    study = generated(p,b,'kpoint', {'grids':[[2,2,2],[2,2,2],[4,4,4],[6,6,6]]})
    points = studies.study_points(p,study['id'])
    assert len(points) == 3 and len({p['point_key'] for p in points}) == 3
    for point in points:
        assert set(json.loads(calculations.get_calculation(p,point['calculation_id']).overrides_json)) <= {'k_points_grid'}
    gamma = generated(p,b,'kpoint', {'mode':'0d_molecule'})
    assert len(studies.study_points(p,gamma['id'])) == 1
    for spec in ({'num_points':3}, {'scales':[.9,1,1,1.1]}, {'start_scale':1,'stop_scale':1}, {'scales':[.9,1,1.1]}, {'num_points':4.5}):
        with pytest.raises(ProjectError): studies.create_study(p,'eos',b.id,spec)


def test_eos_points_use_frozen_reference_and_precise_scale_identity(configured_project):
    p,b = configured_project
    study = studies.create_study(p,'eos',b.id,{'scales':[.99991,.99992,1.00001,1.00002]})
    other = commit_structure(p,bulk('Si',a=8),name='Unrelated active cell')
    set_active_structure(open_project(p.root_path),other.id)
    studies.generate_study(p,study['id'],expected_revision=1)
    reference = load_structure(p,b.structure_id)
    ids = []
    for point in studies.study_points(p,study['id']):
        calc = calculations.get_calculation(p,point['calculation_id'])
        baseline = get_baseline(p,calc.baseline_id)
        assert calc.kind == 'scf' and json.loads(calc.overrides_json) == {}
        assert baseline.pseudo_set_id == b.pseudo_set_id
        assert baseline.settings_json == b.settings_json
        scaled = load_structure(p,baseline.structure_id)
        values = json.loads(point['values_json'])
        assert scaled.get_volume() == pytest.approx(reference.get_volume() * values['scale_factor']**3)
        ids.append(baseline.structure_id)
    assert len(set(ids)) == 4
    assert open_project(p.root_path).active_structure_id == other.id


def test_batch_uses_shared_slot_continues_failures_and_retry_retains_outputs(configured_project):
    p,b = configured_project
    study = generated(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    script = """
import json, time
from pathlib import Path
time.sleep(.15)
cutoff=json.loads(Path('metadata.json').read_text())['resolved_request']['ecutwfc']
if cutoff == 50:
    print('point failed',flush=True)
    raise SystemExit(7)
""" + SCF_SCRIPT
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',script]})
    with pytest.raises(ProjectError): coordinator._acquire_slot()
    points = finish_batch(p,study)
    assert [r['execution_state'] for r in points] == ['completed','failed','completed']
    failed = execution.get_run(p,points[1]['run_id'])
    root = p.root_path / failed['work_relpath']
    before = {f.relative_to(root):f.read_bytes() for f in root.rglob('*') if f.is_file()}
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]})
    points = finish_batch(p,study)
    assert [r['attempt'] for r in points] == [1,2,1]
    assert all(r['execution_state'] == 'completed' for r in points)
    assert {f.relative_to(root):f.read_bytes() for f in root.rglob('*') if f.is_file()} == before
    assert studies.refresh_results(p,study['id'])


def test_batch_cancel_leaves_following_points_unlaunched(configured_project):
    p,b = configured_project
    study = generated(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c','import time;time.sleep(30)']})
    for _ in range(100):
        if any(r['execution_state']=='running' for r in studies.point_statuses(p,study['id'])): break
        time.sleep(.02)
    coordinator.cancel_batch(p,study['id'])
    rows = finish_batch(p,study)
    assert all(r['execution_state']=='cancelled' for r in rows)
    for point in rows[1:]:
        assert execution.run_steps(p,point['run_id'])[0]['started_at'] is None


def test_restart_keeps_queued_study_points_for_explicit_resume(configured_project,monkeypatch):
    p,b=configured_project
    study=generated(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    member=studies.study_points(p,study['id'])[0]
    queued=execution.create_run(p,member['calculation_id'],expected_revision=1,commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]})
    coordinator.reconcile_runs(open_project(p.root_path))
    assert execution.get_run(p,queued['id'])['state']=='queued'
    assert len(execution.list_runs(p))==1
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]})
    rows=finish_batch(p,study)
    assert rows[0]['run_id']==queued['id'] and all(r['attempt']==1 for r in rows)


def test_result_revisions_retain_exact_runs_and_recommendations(configured_project):
    p,b = configured_project
    study = generated(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    missing = studies.refresh_results(p,study['id'])
    assert json.loads(missing['summary_json'])['status'] == 'incomplete'
    assert all(r['execution_state']=='not_run' for r in json.loads(missing['summary_json'])['points'])
    preserved = (p.root_path/missing['payload_relpath']).read_bytes()
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]})
    finish_batch(p,study)
    result = studies.refresh_results(p,study['id'])
    summary = json.loads(result['summary_json'])
    assert result['revision']==2 and summary['recommendation']['recommended_ecutwfc_ry']==40
    assert (p.root_path/missing['payload_relpath']).read_bytes()==preserved
    assert len(plot_cutoff_convergence(summary).data) == 1
    with project_connection(p) as db:
        sources = list(db.execute('SELECT * FROM study_result_sources WHERE result_id=?',(result['id'],)))
    assert all(s['run_id'] and json.loads(s['source_json'])['sha256'] for s in sources)
    point = studies.point_statuses(p,study['id'])[0]
    run = execution.get_run(p,point['run_id'])
    output = p.root_path/run['work_relpath']/'pw.out'
    original = output.read_bytes(); output.unlink()
    damaged = json.loads(studies.refresh_results(p,study['id'])['summary_json'])
    assert damaged['points'][0]['status']=='integrity_error' and damaged['status']=='incomplete'
    output.write_bytes(original)
    assert studies.get_result(p,result['id']) == result
    other = create_project(p.root_path.parent,'Other')
    with pytest.raises(ProjectError): studies.get_result(other,result['id'])
    with pytest.raises(ProjectError): studies.refresh_results(p,study['id'],run_selection={2:point['run_id']})


def test_eos_fit_and_derivation_use_exact_result_and_reference(configured_project):
    p,b = configured_project
    study = generated(p,b,'eos')
    reference = load_structure(p,b.structure_id)
    volume = reference.get_volume()
    from qe_studio.convergence.eos_fit import birch_murnaghan
    from qe_studio.convergence.result_parser import RY_TO_EV
    for point in studies.study_points(p,study['id']):
        values = json.loads(point['values_json'])
        energy_ry = float(birch_murnaghan(values['volume'], -100, volume, .6, 4)) / RY_TO_EV
        script = SCF_SCRIPT.replace('-10.5', repr(energy_ry))
        run = coordinator.submit_calculation(p,point['calculation_id'],expected_revision=1,commands={'pw.x':[sys.executable,'-c',script]})
        assert finish(p,run)['state']=='completed'
    result = studies.refresh_results(p,study['id'])
    summary = json.loads(result['summary_json'])
    assert summary['fit']['success'] and summary['fit']['V0']==pytest.approx(volume,rel=1e-5)
    assert summary['fit']['natoms_in_fitted_cell'] == len(reference)
    assert len(plot_eos_study(summary,display_mode='per_atom').data)==2
    other = commit_structure(p,bulk('Si',a=9),name='Other cell')
    set_active_structure(open_project(p.root_path),other.id)
    derived = studies.derive_equilibrium_structure(p,result['id'])
    assert derived.parent_structure_id == b.structure_id
    assert load_structure(p,derived.id).get_volume() == pytest.approx(volume,rel=1e-5)
    assert studies.derive_equilibrium_structure(p,result['id']).id == derived.id
    assert open_project(p.root_path).active_structure_id == other.id


def test_kpoint_results_keep_existing_algorithm_and_can_reopen_after_move(configured_project,tmp_path):
    p,b=configured_project
    study=generated(p,b,'kpoint')
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]})
    finish_batch(p,study)
    result=studies.refresh_results(p,study['id'])
    summary=json.loads(result['summary_json'])
    assert summary['recommendation']['recommended_k_grid']==[2,2,2]
    assert len(plot_kpoint_convergence(summary).data)==1
    moved=tmp_path/'Moved';p.root_path.rename(moved)
    reopened=open_project(moved)
    assert studies.get_result(reopened,result['id'])==result
    assert all(not Path(row['output_path']).is_absolute() for row in summary['points'])
