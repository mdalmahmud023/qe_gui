import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from test_calculations import configured_project
from qe_studio.core.project_models import ProjectError
from qe_studio.core.project_session import activate_context
from qe_studio.services import calculation_service as calculations, execution_service as execution, execution_coordinator as coordinator, qe_runner
from qe_studio.storage.project_repository import project_connection
from qe_studio.services.project_service import open_project

SCF_SCRIPT = '''
import json
from pathlib import Path
meta=json.loads(Path('metadata.json').read_text())
prefix=meta['resolved_request']['prefix']
save=Path('tmp')/(prefix+'.save');save.mkdir(parents=True,exist_ok=True)
(save/'data-file-schema.xml').write_text('<root><eigenvalues>1 2</eigenvalues></root>')
(save/'charge-density.dat').write_text('charge data')
print('convergence has been achieved in 5 iterations')
print('!    total energy = -10.5 Ry')
print('JOB DONE.')
'''
BANDS_SCRIPT = "from pathlib import Path;Path('filband.gnu').write_text('0 -1\\n1 0\\n\\n0 1\\n1 2\\n'); print('JOB DONE.')"
DOS_SCRIPT = "from pathlib import Path;Path('dos.dat').write_text('# E DOS IntDOS\\n-1 1 0\\n0 2 1\\n1 1 2\\n'); print('JOB DONE.')"


@pytest.fixture(autouse=True)
def isolate_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('QE_STUDIO_WORKSPACE_DIR', str(tmp_path / 'machine'))
    yield
    for context in list(qe_runner.RUN_REGISTRY.values()):
        if context.thread.is_alive():
            if context.process:
                try:
                    os.killpg(context.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            context.thread.join(timeout=5)


def finish(project, run):
    context = qe_runner.get_run_context(run['id'])
    assert context is not None
    context.thread.join(timeout=15)
    assert not context.thread.is_alive()
    return execution.get_run(project, run['id'])


def generated(project, baseline, kind='scf', *, parent=None, overrides=None):
    c = calculations.create_calculation(project, kind, baseline.id, overrides=overrides, parent_run_id=parent)
    return calculations.generate_calculation(project, c.id, expected_revision=1)


def launch_scf(project, baseline):
    c = generated(project, baseline)
    run = coordinator.submit_calculation(project, c.id, expected_revision=1, commands={'pw.x':[sys.executable, '-c', SCF_SCRIPT]})
    return c, finish(project, run)


def test_first_attempt_freezes_science_and_retry_preserves_all_files(configured_project):
    p, b = configured_project
    c, first = launch_scf(p, b)
    assert first['state'] == 'completed'
    frozen = calculations.get_calculation(p, c.id)
    assert frozen.frozen_at
    with pytest.raises(ProjectError, match='Duplicate'):
        calculations.save_draft(p, c.id, {'ecutwfc':60}, expected_revision=1)
    with pytest.raises(ProjectError):
        calculations.generate_calculation(p, c.id, expected_revision=1)
    renamed = calculations.rename_calculation(p, c.id, 'Frozen renamed', notes='annotation')
    assert renamed.definition_hash == c.definition_hash
    root = p.root_path / first['work_relpath']
    before = {f.relative_to(root): f.read_bytes() for f in root.rglob('*') if f.is_file()}
    second = finish(p, coordinator.submit_calculation(p, c.id, expected_revision=1, commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]}))
    assert second['attempt'] == 2 and second['state'] == 'completed'
    assert {f.relative_to(root): f.read_bytes() for f in root.rglob('*') if f.is_file()} == before
    duplicate = calculations.duplicate_calculation(p,c.id)
    assert duplicate.frozen_at is None and duplicate.generated_revision is None
    assert execution.artifacts(p, first['id'])


def test_failed_run_keeps_output_and_exit_code(configured_project):
    p,b=configured_project
    c=generated(p,b)
    run=finish(p,coordinator.submit_calculation(p,c.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',"print('failure diagnostic');raise SystemExit(7)"]}))
    assert run['state']=='failed' and run['exit_code']==7
    assert 'failure diagnostic' in (p.root_path/run['work_relpath']/'pw.out').read_text()
    assert (p.root_path/run['work_relpath']/'pw.in').is_file()
    assert (p.root_path/run['work_relpath']/'logs/events.jsonl').is_file()


def test_successful_process_without_convergence_is_scientific_failure(configured_project):
    p,b=configured_project;c=generated(p,b)
    run=finish(p,coordinator.submit_calculation(p,c.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',"print('JOB DONE.')"]}))
    assert run['state']=='failed' and run['exit_code']==0
    assert 'convergence' in run['failure_message']


def test_explicit_parent_copy_bands_two_steps_and_dos_chain(configured_project):
    p,b=configured_project;c,parent=launch_scf(p,b)
    assert parent['state']=='completed'
    source=execution.parent_restart(p,parent['id']);before=execution.content_hash(source)
    bands=generated(p,b,'bands',parent=parent['id'],overrides={'nbnd':8,'band_path':'0 0 0 2\n0.5 0 0 1'})
    bandrun=finish(p,coordinator.submit_calculation(p,bands.id,expected_revision=1,
        commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT], 'bands.x':[sys.executable,'-c',BANDS_SCRIPT]}))
    assert bandrun['state']=='completed',bandrun['failure_message']
    assert [step['kind'] for step in execution.run_steps(p,bandrun['id'])]==['bands','bands_pp']
    nscf=generated(p,b,'nscf',parent=parent['id'],overrides={'nbnd':8,'k_points_grid':[2,2,2]})
    nscfrun=finish(p,coordinator.submit_calculation(p,nscf.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]}))
    dos=generated(p,b,'dos',parent=nscfrun['id'])
    dosrun=finish(p,coordinator.submit_calculation(p,dos.id,expected_revision=1,commands={'dos.x':[sys.executable,'-c',DOS_SCRIPT]}))
    assert dosrun['state']=='completed',dosrun['failure_message']
    assert execution.content_hash(source)==before
    assert (p.root_path/nscfrun['work_relpath']/'tmp'/source.name).stat().st_ino != source.stat().st_ino


def test_cancel_prevents_next_step_and_switching_does_not_reassign(configured_project):
    p,b=configured_project;_,parent=launch_scf(p,b)
    c=generated(p,b,'bands',parent=parent['id'],overrides={'nbnd':8,'band_path':'0 0 0 2\n0.5 0 0 1'})
    run=coordinator.submit_calculation(p,c.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',"import time;print('started',flush=True);time.sleep(30)"], 'bands.x':[sys.executable,'-c',BANDS_SCRIPT]})
    for _ in range(100):
        if execution.get_run(p,run['id'])['process_json']!='{}': break
        time.sleep(.02)
    state={};activate_context(state,p.id);activate_context(state,'other-project')
    assert execution.get_run(p,run['id'])['state']=='running'
    with pytest.raises(ProjectError,match='execution slot'):
        coordinator.submit_calculation(p,c.id,expected_revision=1)
    coordinator.cancel_run(p,run['id']);run=finish(p,run)
    assert run['state']=='cancelled'
    steps=execution.run_steps(p,run['id'])
    assert steps[1]['started_at'] is None and steps[1]['state']=='cancelled'
    assert not (p.root_path/run['work_relpath']/'bands_pp.out').exists()


def test_restart_reconciliation_never_retries_uncertain_record(configured_project,monkeypatch):
    p,b=configured_project;c=generated(p,b)
    run=execution.create_run(p,c.id,expected_revision=1)
    with project_connection(p,write=True) as db:
        db.execute("UPDATE runs SET state='running',process_json=? WHERE id=?",(json.dumps(coordinator.process_identity(os.getpid())),run['id']))
    monkeypatch.setattr(qe_runner,'run_chained_jobs',lambda **kwargs: pytest.fail('Restart must not launch anything'))
    coordinator.reconcile_runs(open_project(p.root_path))
    recovered=execution.get_run(p,run['id'])
    assert recovered['state']=='interrupted' and 'still alive' in recovered['failure_message']
    assert len(execution.list_runs(p))==1
    with pytest.raises(ProjectError):coordinator.submit_run(p,run['id'])


def test_outdated_input_cannot_freeze_and_corrupt_parent_blocks_child(configured_project):
    p,b=configured_project;c,parent=launch_scf(p,b)
    draft=calculations.duplicate_calculation(p,c.id)
    with pytest.raises(ProjectError):execution.create_run(p,draft.id,expected_revision=1)
    assert calculations.get_calculation(p,draft.id).frozen_at is None
    source=execution.parent_restart(p,parent['id']);(source/'charge-density.dat').write_text('changed')
    child=calculations.create_calculation(p,'nscf',b.id,parent_run_id=parent['id'])
    with pytest.raises(ProjectError,match='changed'):calculations.generate_calculation(p,child.id,expected_revision=1)


def test_cancellation_between_steps_never_starts_postprocessor(configured_project, monkeypatch):
    p, b = configured_project
    _, parent = launch_scf(p, b)
    c = generated(p, b, 'bands', parent=parent['id'], overrides={'nbnd':8, 'band_path':'0 0 0 2\n0.5 0 0 1'})
    validate = execution.validate_step
    def cancel_after_validation(project, run_id, step):
        validate(project, run_id, step)
        coordinator.cancel_run(project, run_id)
    monkeypatch.setattr(execution, 'validate_step', cancel_after_validation)
    run = finish(p, coordinator.submit_calculation(p, c.id, expected_revision=1,
        commands={'pw.x':[sys.executable, '-c', SCF_SCRIPT], 'bands.x':[sys.executable, '-c', BANDS_SCRIPT]}))
    assert run['state'] == 'cancelled'
    assert execution.run_steps(p, run['id'])[1]['started_at'] is None
    assert not (p.root_path / run['work_relpath'] / 'bands_pp.out').exists()


def test_stdout_inherited_by_helper_does_not_block_completion(configured_project):
    p, b = configured_project
    c = generated(p, b)
    script = """
import subprocess, sys
from pathlib import Path
helper = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
Path('helper.pid').write_text(str(helper.pid))
""" + SCF_SCRIPT
    run = coordinator.submit_calculation(p, c.id, expected_revision=1, commands={'pw.x':[sys.executable, '-c', script]})
    try:
        run = finish(p, run)
        assert run['state'] == 'completed'
        assert 'JOB DONE.' in (p.root_path / run['work_relpath'] / 'pw.out').read_text()
    finally:
        pidfile = p.root_path / run['work_relpath'] / 'helper.pid'
        if pidfile.exists():
            try:
                os.killpg(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_surviving_process_lease_blocks_new_owner_without_inherited_lock(tmp_path):
    handle = coordinator._acquire_slot()
    coordinator._write_lease(handle, {'released':False, 'process':coordinator.process_identity(os.getpid())})
    coordinator._release_slot(handle)
    with pytest.raises(ProjectError, match='still alive'):
        coordinator._acquire_slot()
    # A rejected claim must close its descriptor, so a cleared lease can reopen.
    lease = tmp_path / 'machine/runtime/execution.json'
    lease.write_text(json.dumps({'released':True}))
    coordinator._release_slot(coordinator._acquire_slot())


def test_failed_launch_and_ownership_publication_retain_attempt(configured_project, monkeypatch):
    p, b = configured_project
    c = generated(p, b)
    run = finish(p, coordinator.submit_calculation(p, c.id, expected_revision=1,
        commands={'pw.x':['/no-such-qe-studio-executable']}))
    assert run['state'] == 'failed' and run['exit_code'] is None
    assert (p.root_path / run['work_relpath'] / 'pw.in').exists()
    write_lease = coordinator._write_lease
    def fail(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(coordinator, '_write_lease', fail)
    with pytest.raises(OSError, match='disk failure'):
        coordinator.submit_calculation(p, c.id, expected_revision=1)
    second = execution.list_runs(p, c.id)[-1]
    assert second['state'] == 'failed' and second['attempt'] == 2
    assert (p.id, second['id']) not in coordinator.LIVE_ATTEMPTS
    monkeypatch.setattr(coordinator, '_write_lease', write_lease)
    coordinator._release_slot(coordinator._acquire_slot())


def test_cross_project_runs_and_parents_are_rejected(configured_project, tmp_path):
    from qe_studio.services.project_service import create_project
    from qe_studio.services.structure_service import commit_structure
    from qe_studio.services.pseudo_set_service import commit_pseudo_set
    from qe_studio.services.baseline_service import commit_baseline
    from ase.build import bulk
    p, b = configured_project
    _, parent = launch_scf(p, b)
    other = create_project(tmp_path, 'Other')
    structure = commit_structure(other, bulk('Si'), name='Si')
    pseudo = commit_pseudo_set(other, {'Si':{'local_path':str(tmp_path / 'Si.UPF')}})
    baseline = commit_baseline(other, structure.id, pseudo.id, {})
    with pytest.raises(ProjectError, match='opened project'):
        calculations.create_calculation(other, 'nscf', baseline.id, parent_run_id=parent['id'])
    with pytest.raises(ProjectError):
        coordinator.submit_run(other, parent['id'])
    _, run = launch_scf(other, baseline)
    assert run['state'] == 'completed'
    assert execution.get_run(p, parent['id'])['state'] == 'completed'
    assert execution.parent_restart(other, run['id']).is_relative_to(other.root_path)
    assert execution.parent_restart(p, parent['id']).is_relative_to(p.root_path)


def test_inherited_eigenvalues_do_not_certify_a_noop_nscf(configured_project):
    p, b = configured_project
    _, parent = launch_scf(p, b)
    c = generated(p, b, 'nscf', parent=parent['id'])
    run = finish(p, coordinator.submit_calculation(p, c.id, expected_revision=1,
        commands={'pw.x':[sys.executable, '-c', "print('JOB DONE.')"]}))
    assert run['state'] == 'failed' and run['exit_code'] == 0
    assert 'inherited eigenvalue' in run['failure_message']


def test_project_stream_is_bounded_without_losing_disk_log(configured_project):
    p, b = configured_project
    c = generated(p, b)
    script = "for i in range(1000): print('diagnostic line', i)\n" + SCF_SCRIPT
    run = finish(p, coordinator.submit_calculation(p, c.id, expected_revision=1,
        commands={'pw.x':[sys.executable, '-c', script]}))
    assert run['state'] == 'completed'
    assert qe_runner.get_run_context(run['id']).queue.qsize() <= qe_runner.DEFAULT_MAX_TAIL_LINES
    output = (p.root_path / run['work_relpath'] / 'pw.out').read_text()
    assert output.count('diagnostic line') == 1000 and 'JOB DONE.' in output


def test_frozen_definition_and_run_specs_reject_direct_sql_updates(configured_project):
    import sqlite3
    p, b = configured_project
    c = generated(p, b)
    run = execution.create_run(p, c.id, expected_revision=1)
    for statement, parameters in [
        ("UPDATE calculations SET overrides_json='{}',draft_revision=2 WHERE id=?", (c.id,)),
        ("UPDATE runs SET work_relpath='other' WHERE id=?", (run['id'],)),
        ("UPDATE run_steps SET command_json='[]' WHERE run_id=?", (run['id'],)),
    ]:
        with pytest.raises(sqlite3.IntegrityError), project_connection(p, write=True) as db:
            db.execute(statement, parameters)
    coordinator.cancel_run(p, run['id'])
    assert execution.get_run(p, run['id'])['state'] == 'cancelled'
    with pytest.raises(ProjectError):
        coordinator.submit_run(p, run['id'])
