from dataclasses import replace
import json
import shutil
import sys

from ase.build import bulk
import pytest

from test_calculations import configured_project
from test_project_execution import isolate_runtime, launch_scf, generated, finish, SCF_SCRIPT, DOS_SCRIPT
from test_project_studies import generated as generated_study, finish_batch
from qe_studio.core.project_models import ProjectError
from qe_studio.services import dependency_service as deps, calculation_service as calculations, execution_service as execution, execution_coordinator as coordinator
from qe_studio.services.baseline_service import commit_baseline, set_active_baseline
from qe_studio.services.structure_service import commit_structure
from qe_studio.services.pseudo_set_service import commit_pseudo_set
from qe_studio.services.project_service import open_project, create_project
from qe_studio.services.studies import study_service as studies
from qe_studio.storage.project_repository import project_connection


def revised(project, baseline, **settings):
    return commit_baseline(project, baseline.structure_id, baseline.pseudo_set_id,
        {**json.loads(baseline.settings_json), **settings}, previous_baseline_id=baseline.id)


def test_equivalent_baseline_ids_and_cosmetic_settings_are_current(configured_project):
    p,b=configured_project
    calc=generated(p,b)
    equivalent=revised(p,b,spin_source='user')
    report=deps.evaluate(p,'calculation',calc.id,baseline_id=equivalent.id)
    assert report.state==deps.Freshness.CURRENT and report.policy_version==1
    key=report.evaluation_key
    calculations.rename_calculation(p,calc.id,'Renamed',notes='new label')
    assert deps.evaluate(p,'calculation',calc.id,baseline_id=equivalent.id).evaluation_key==key
    assert deps.evaluate(p,'calculation',calc.id).state==deps.Freshness.UNKNOWN
    set_active_baseline(open_project(p.root_path),equivalent.id)
    assert deps.evaluate(p,'calculation',calc.id).state==deps.Freshness.CURRENT


@pytest.mark.parametrize('field,value',[
    ('nspin',2),('noncolin',True),('lspinorb',True),('dftu_enabled',True),
    ('vdw_method','dft-d3'),('occupations','smearing'),('ecutwfc',100),('ecutrho',1000),
    ('conv_thr',1e-9),('mixing_beta',.3),('k_points_grid',[12,12,12]),
])
def test_physical_and_numerical_projections_detect_changes(configured_project,field,value):
    p,b=configured_project
    old=deps.baseline_snapshot(p,b.id)
    new=json.loads(json.dumps(old))
    if new['settings'][field]==value:
        new['settings'][field]='fixed' if field=='occupations' else None
    else:
        new['settings'][field]=value
    state,reasons=deps.compare_snapshots(old,new)
    assert state==deps.Freshness.STALE and any(r.field==field for r in reasons)


def test_enabled_hubbard_and_magnetization_ignore_source_labels(configured_project):
    p,b=configured_project
    a=deps.baseline_snapshot(p,b.id)
    a['settings'].update(nspin=2,starting_magnetization={'Si':{'value':.5,'source':'default'}},dftu_enabled=True,hubbard_u={'Si':{'u_ev':4,'source':'user'}})
    b=json.loads(json.dumps(a));b['settings']['starting_magnetization']['Si']['source']='manual'
    b['settings']['hubbard_u']['Si']['source']='imported'
    assert deps.compare_snapshots(a,b)[0]==deps.Freshness.CURRENT
    b['settings']['hubbard_u']['Si']['u_ev']=6
    assert any(r.field=='hubbard_u' for r in deps.compare_snapshots(a,b)[1])
    # Incomplete historical evidence is not filled from current defaults.
    b['settings']={}
    assert deps.compare_snapshots(b,b)[0]==deps.Freshness.UNKNOWN


def test_structure_change_propagates_to_parent_chain_without_rewriting_runs(configured_project):
    p,b=configured_project
    scf,scfrun=launch_scf(p,b)
    nscf=generated(p,b,'nscf',parent=scfrun['id'],overrides={'nbnd':8,'k_points_grid':[2,2,2]})
    nrun=finish(p,coordinator.submit_calculation(p,nscf.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]}))
    dos=generated(p,b,'dos',parent=nrun['id'])
    drun=finish(p,coordinator.submit_calculation(p,dos.id,expected_revision=1,commands={'dos.x':[sys.executable,'-c',DOS_SCRIPT]}))
    before={r['id']:r for r in execution.list_runs(p)}
    structure=commit_structure(p,bulk('Si',a=6),name='New geometry')
    new=commit_baseline(p,structure.id,b.pseudo_set_id,json.loads(b.settings_json))
    for calc in (scf,nscf,dos):
        report=deps.evaluate(p,'calculation',calc.id,baseline_id=new.id)
        assert report.state==deps.Freshness.STALE
        assert any(r.field=='structure_hash' for r in report.reasons)
        if calc.kind!='scf':
            assert any(r.upstream_id for r in report.reasons)
        assert deps.evaluate(p,'calculation',calc.id,baseline_id=b.id).state==deps.Freshness.CURRENT
    assert {r['id']:r for r in execution.list_runs(p)}==before
    assert all(r['state']=='completed' for r in before.values())
    assert any(link.relationship==deps.Relationship.PARENT_RUN and link.upstream_id==nrun['id'] for link in deps.dependencies(p,'calculation',dos.id))


def test_new_upf_set_is_staleness_but_damaged_old_bytes_are_integrity(configured_project,tmp_path):
    p,b=configured_project;calc,run=launch_scf(p,b)
    source=tmp_path/'new.UPF';source.write_text('new Si pseudo bytes')
    new_pseudo=commit_pseudo_set(p,{'Si':{'local_path':str(source)}})
    new_baseline=commit_baseline(p,b.structure_id,new_pseudo.id,json.loads(b.settings_json))
    report=deps.evaluate(p,'calculation',calc.id,baseline_id=new_baseline.id)
    assert report.state==deps.Freshness.STALE and any('Si pseudopotential changed' in r.message for r in report.reasons)
    assert not any(f.integrity=='corrupt' for f in report.files)
    with project_connection(p) as db:
        path=db.execute('SELECT relpath FROM pseudo_entries WHERE pseudo_set_id=?',(b.pseudo_set_id,)).fetchone()[0]
    (p.root_path/path).write_text('corrupt same filename')
    report=deps.evaluate(p,'run',run['id'],baseline_id=b.id)
    assert report.state==deps.Freshness.CURRENT
    bad=[f for f in report.files if f.role=='Si pseudopotential' and f.integrity=='corrupt']
    assert bad and bad[0].expected!=bad[0].found
    with pytest.raises(ProjectError):calculations.create_calculation(p,'scf',b.id)
    assert execution.get_run(p,run['id'])['state']=='completed'


def test_missing_restart_is_capability_loss_not_scientific_staleness(configured_project):
    p,b=configured_project;calc,run=launch_scf(p,b)
    child=generated(p,b,'nscf',parent=run['id'])
    shutil.rmtree(execution.parent_restart(p,run['id']))
    parent_report=deps.evaluate(p,'run',run['id'],baseline_id=b.id)
    report=deps.evaluate(p,'calculation',child.id,baseline_id=b.id)
    assert report.state==parent_report.state==deps.Freshness.CURRENT
    assert report.capabilities['parent_restart_ready'] is False
    assert parent_report.capabilities['restart_available'] is False
    assert any(f.role=='output' and f.integrity=='verified' for f in parent_report.files)
    assert not any(f.integrity=='corrupt' for f in parent_report.files)
    with pytest.raises(ProjectError):execution.create_run(p,child.id,expected_revision=1)
    assert execution.get_run(p,run['id'])['state']=='completed'


def test_study_specific_projections_and_eos_reference(configured_project):
    p,b=configured_project
    cutoff=generated_study(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    kpoint=generated_study(p,b,'kpoint')
    eos=generated_study(p,b,'eos',{'num_points':4})
    different_cutoff=revised(p,b,ecutwfc=100,ecutrho=800)
    different_grid=revised(p,b,k_points_grid=[12,12,12])
    assert deps.evaluate(p,'study',cutoff['id'],baseline_id=different_cutoff.id).state==deps.Freshness.CURRENT
    assert deps.evaluate(p,'study',cutoff['id'],baseline_id=different_grid.id).state==deps.Freshness.STALE
    assert deps.evaluate(p,'study',kpoint['id'],baseline_id=different_grid.id).state==deps.Freshness.CURRENT
    assert deps.evaluate(p,'study',kpoint['id'],baseline_id=different_cutoff.id).state==deps.Freshness.STALE
    assert deps.evaluate(p,'study',eos['id'],baseline_id=different_cutoff.id).state==deps.Freshness.STALE
    for point in studies.study_points(p,eos['id']):
        assert deps.evaluate(p,'calculation',point['calculation_id'],baseline_id=b.id).state==deps.Freshness.CURRENT
    assert deps.evaluate(p,'structure',b.structure_id,baseline_id=different_cutoff.id).state==deps.Freshness.CURRENT


def test_missing_resolved_provenance_is_unknown(configured_project,monkeypatch):
    p,b=configured_project;calc=generated(p,b)
    get=calculations.get_calculation
    monkeypatch.setattr(calculations,'get_calculation',lambda project,identifier: replace(get(project,identifier),resolved_request_json='{}'))
    report=deps.evaluate(p,'calculation',calc.id,baseline_id=b.id)
    assert report.state==deps.Freshness.UNKNOWN


def test_duplicate_with_current_baseline_uses_new_science_and_requires_new_parent(configured_project):
    p,b=configured_project;calc,run=launch_scf(p,b)
    changed=revised(p,b,ecutwfc=100,ecutrho=800)
    copied=deps.duplicate_using_baseline(p,calc.id,baseline_id=changed.id)
    assert copied.id!=calc.id and copied.baseline_id==changed.id and copied.frozen_at is None
    assert json.loads(copied.resolved_request_json)['ecutwfc']==100
    child=generated(p,b,'nscf',parent=run['id'],overrides={'nbnd':8,'k_points_grid':[2,2,2]})
    copied=deps.duplicate_using_baseline(p,child.id,baseline_id=changed.id)
    assert copied.parent_run_id is None and json.loads(copied.overrides_json)['nbnd']==8
    assert deps.evaluate(p,'calculation',copied.id,baseline_id=changed.id).state==deps.Freshness.UNKNOWN
    assert execution.get_run(p,run['id'])['state']=='completed'


def test_apply_recommendation_records_exact_result_and_cross_project_isolation(configured_project,tmp_path):
    p,b=configured_project
    study=generated_study(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]});finish_batch(p,study)
    result=studies.refresh_results(p,study['id'])
    before=result['summary_json']
    baseline=deps.apply_recommendation(p,result['id'],baseline_id=b.id)
    links=deps.dependencies(p,'baseline',baseline.id)
    assert any(link.relationship==deps.Relationship.RECOMMENDED_BY and link.upstream_id==result['id'] and set(link.fields)=={'ecutwfc','ecutrho'} for link in links)
    assert json.loads(baseline.settings_json)['ecutwfc']==40
    assert studies.get_result(p,result['id'])['summary_json']==before
    assert any(link.relationship==deps.Relationship.CONSUMED_RUN for link in deps.dependencies(p,'study_result',result['id']))
    incompatible=revised(p,b,k_points_grid=[14,14,14])
    with pytest.raises(ProjectError):deps.apply_recommendation(p,result['id'],baseline_id=incompatible.id)
    other=create_project(tmp_path,'Other')
    with pytest.raises(ProjectError):deps.apply_recommendation(other,result['id'],baseline_id=b.id)
    with pytest.raises(ProjectError):deps.evaluate(other,'study_result',result['id'],baseline_id=b.id)


def test_recommendation_chain_preserves_field_origins_and_sql_immutability(configured_project):
    import sqlite3
    p,b=configured_project
    cutoff=generated_study(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    studies.run_study(p,cutoff['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]});finish_batch(p,cutoff)
    result=studies.refresh_results(p,cutoff['id'])
    cutoff_baseline=deps.apply_recommendation(p,result['id'],baseline_id=b.id)
    kpoint=generated_study(p,cutoff_baseline,'kpoint')
    studies.run_study(p,kpoint['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]});finish_batch(p,kpoint)
    kresult=studies.refresh_results(p,kpoint['id'])
    final=deps.apply_recommendation(p,kresult['id'],baseline_id=cutoff_baseline.id)
    links=deps.baseline_provenance(p,final.id)
    assert {(link.upstream_id,link.fields) for link in links if link.relationship==deps.Relationship.RECOMMENDED_BY}=={
        (result['id'],('ecutwfc','ecutrho')),(kresult['id'],('k_points_grid',))}
    changed=revised(p,final,ecutwfc=100,ecutrho=1000)
    assert all(link.upstream_id!=result['id'] for link in deps.baseline_provenance(p,changed.id))
    with pytest.raises(sqlite3.IntegrityError),project_connection(p,write=True) as db:
        db.execute("UPDATE baseline_recommendations SET fields_json='[]'")


def test_eos_application_records_structure_and_baseline_sources(configured_project):
    from qe_studio.convergence.eos_fit import birch_murnaghan
    from qe_studio.convergence.result_parser import RY_TO_EV
    from qe_studio.services.structure_service import load_structure
    p,b=configured_project
    study=generated_study(p,b,'eos')
    volume=load_structure(p,b.structure_id).get_volume()
    for point in studies.study_points(p,study['id']):
        v=json.loads(point['values_json'])['volume']
        energy=float(birch_murnaghan(v,-100,volume,.6,4))/RY_TO_EV
        run=coordinator.submit_calculation(p,point['calculation_id'],expected_revision=1,
            commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT.replace('-10.5',repr(energy))]})
        assert finish(p,run)['state']=='completed'
    result=studies.refresh_results(p,study['id'])
    new=deps.apply_recommendation(p,result['id'],baseline_id=b.id)
    assert new.structure_id!=b.structure_id
    assert any(link.upstream_id==result['id'] and link.relationship==deps.Relationship.DERIVED_FROM for link in deps.dependencies(p,'structure',new.structure_id))
    assert any(link.upstream_id==result['id'] and link.fields==('structure_id',) for link in deps.dependencies(p,'baseline',new.id))
    assert open_project(p.root_path).active_baseline_id is None


def test_recommendation_and_baseline_commit_roll_back_together(configured_project,monkeypatch):
    import sqlite3
    p,b=configured_project
    study=generated_study(p,b,spec={'ecutwfc_values_ry':[40,50,60]})
    studies.run_study(p,study['id'],commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]});finish_batch(p,study)
    result=studies.refresh_results(p,study['id'])
    with project_connection(p) as db:
        count=db.execute('SELECT count(*) FROM baselines').fetchone()[0]
    commit=deps.commit_baseline
    def inject_bad_reference(*args,**kwargs):
        kwargs['_recommendation']=('foreign-result-id',['ecutwfc'],1)
        return commit(*args,**kwargs)
    monkeypatch.setattr(deps,'commit_baseline',inject_bad_reference)
    with pytest.raises(sqlite3.IntegrityError):deps.apply_recommendation(p,result['id'],baseline_id=b.id)
    with project_connection(p) as db:
        assert db.execute('SELECT count(*) FROM baselines').fetchone()[0]==count
        assert db.execute('SELECT count(*) FROM baseline_recommendations').fetchone()[0]==0
