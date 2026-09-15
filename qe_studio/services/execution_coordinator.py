"""One project-mode execution owner, using qe_runner threads (no daemon).

The advisory lock is inherited by the QE process. If the Streamlit process
exits unexpectedly, a surviving QE child still holds the slot. Reconciliation
never interprets loss of an in-memory handle as evidence that QE stopped.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time
from uuid import uuid4

from ..core.project_models import ProjectError
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import canonical_json
from ..storage.project_repository import project_connection
from ..storage.workspace_store import workspace_database_path
from . import qe_runner
from . import execution_service as execution
from .calculation_service import now, get_calculation, generated_inputs

LIVE_ATTEMPTS = {}
LIVE_LOCK = threading.RLock()
BATCHES = {}


def process_identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'host': socket.gethostname(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'start_ticks': stat[19], 'process_state': stat[0]}
    except (OSError, IndexError):
        return {'pid': pid, 'host': socket.gethostname(), 'unknown': True}


def identity_alive(identity):
    if not identity or identity.get('unknown') or identity.get('host') != socket.gethostname():
        return False
    current = process_identity(identity['pid'])
    return current.get('process_state') != 'Z' and all(current.get(key) == identity.get(key) for key in ('pid', 'host', 'boot_id', 'start_ticks'))


def _acquire_slot():
    root = workspace_database_path().parent / 'runtime'
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / 'execution.lock').open('a+')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ProjectError('Another project job owns the execution slot. It may still be running after monitoring was lost.') from exc
    lease_path = root / 'execution.json'
    if lease_path.exists():
        try:
            lease = json.loads(lease_path.read_text())
            if not lease.get('released') and (identity_alive(lease.get('process')) or identity_alive(lease.get('controller'))):
                raise ProjectError('A prior project execution owner is still alive. Lost monitoring does not release ownership.')
        except (ValueError, OSError):
            handle.close()
            raise
    return handle


def _write_lease(handle, payload):
    root = Path(handle.name).parent
    temporary = root / ('.execution-' + uuid4().hex + '.json')
    temporary.write_text(canonical_json(payload))
    temporary.replace(root / 'execution.json')


def _release_slot(handle):
    # Close without LOCK_UN: an inherited child descriptor must keep ownership
    # until that process also exits if our worker lost control unexpectedly.
    handle.close()


def submit_calculation(project, calculation_id, *, expected_revision, commands=None):
    handle = _acquire_slot()
    try:
        run = execution.create_run(project, calculation_id, expected_revision=expected_revision, commands=commands)
        return _launch(project, run['id'], handle)
    except BaseException:
        if not handle.closed:
            _release_slot(handle)
        raise


def submit_run(project, run_id):
    handle = _acquire_slot()
    try:
        return _launch(project, run_id, handle)
    except BaseException:
        if not handle.closed:
            _release_slot(handle)
        raise


def _launch(project, run_id, handle, *, release_on_complete=True):
    run = execution.get_run(project, run_id)
    if run['state'] != 'queued':
        raise ProjectError('Only a queued attempt can be launched. Create a new attempt to retry.')
    calculation = get_calculation(project, run['calculation_id'])
    generated_inputs(project, calculation)
    root = ProjectPaths(project.root_path).resolve_relative(run['work_relpath'])
    for artifact in execution.artifacts(project, run_id):
        path = ProjectPaths(project.root_path).resolve_relative(artifact['relpath'])
        if execution.content_hash(path) != artifact['sha256']:
            raise ProjectError('Staged run input or parent restart data changed. This attempt cannot launch.')
        if artifact['role'] == 'parent_restart_snapshot':
            receipt = json.loads(path.read_text())
            staged = ProjectPaths(project.root_path).resolve_relative(run['work_relpath'] + '/' + receipt['staged_relpath'])
            if execution.content_hash(staged) != receipt['sha256']:
                raise ProjectError('The staged parent restart copy changed before launch.')
    execution.validate_parent(project, calculation)
    from .baseline_service import get_baseline
    from .pseudo_set_service import load_pseudo_entries
    baseline = get_baseline(project, calculation.baseline_id)
    load_pseudo_entries(project, baseline.pseudo_set_id)
    cancel_event = threading.Event()
    with project_connection(project, write=True) as db:
        cursor = db.execute("UPDATE runs SET state='running',runtime_id=?,controller_json=?,started_at=? WHERE id=? AND state='queued'",
                            (run_id, canonical_json(process_identity(os.getpid())), now(), run_id))
        if cursor.rowcount != 1:
            raise ProjectError('Another tab already claimed this attempt.')
    lease = {'project_id': project.id, 'project_root': str(project.root_path), 'run_id': run_id,
             'controller': process_identity(os.getpid()), 'process': {}, 'released': False}
    try:
        _write_lease(handle, lease)
    except Exception as exc:
        # No subprocess has started. Retain the frozen attempt and its inputs.
        with project_connection(project, write=True) as db:
            db.execute("UPDATE runs SET state='failed',finished_at=?,failure_message=? WHERE id=?",
                       (now(), 'Execution ownership publication failed: ' + str(exc), run_id))
            db.execute("UPDATE run_steps SET state='failed',finished_at=? WHERE run_id=?", (now(), run_id))
        raise
    with LIVE_LOCK:
        LIVE_ATTEMPTS[(project.id, run_id)] = {'cancel': cancel_event, 'handle': handle}

    def observer(event):
        completed = event['event'] == 'complete'
        try:
            with project_connection(project, write=True) as db:
                row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
                if row['cancel_requested']:
                    cancel_event.set()
                kind = event['event']; ordinal = event['ordinal']
                if kind == 'step_started':
                    db.execute("UPDATE run_steps SET state='running',started_at=? WHERE run_id=? AND ordinal=?", (now(), run_id, ordinal))
                elif kind == 'process_started':
                    db.execute('UPDATE runs SET process_json=? WHERE id=?', (canonical_json(process_identity(event['pid'])), run_id))
                    lease['process'] = process_identity(event['pid'])
                    _write_lease(handle, lease)
                elif kind == 'process_exited':
                    db.execute('UPDATE run_steps SET exit_code=? WHERE run_id=? AND ordinal=?', (event['returncode'], run_id, ordinal))
                    db.execute('UPDATE runs SET exit_code=?,process_json=? WHERE id=?', (event['returncode'], '{}', run_id))
                elif kind == 'step_completed':
                    db.execute("UPDATE run_steps SET state='completed',finished_at=? WHERE run_id=? AND ordinal=?", (now(), run_id, ordinal))
                elif completed:
                    state = 'cancelled' if cancel_event.is_set() else event['state']
                    message = event.get('message')
                    db.execute('UPDATE runs SET state=?,finished_at=?,failure_message=? WHERE id=?', (state, now(), message, run_id))
                    db.execute("UPDATE run_steps SET state=?,finished_at=?,failure_message=? WHERE run_id=? AND ordinal=? AND state!='completed'",
                               (state, now(), message, run_id, ordinal))
                    db.execute("UPDATE run_steps SET state=?,finished_at=? WHERE run_id=? AND state='queued'", ('cancelled' if state == 'cancelled' else 'interrupted', now(), run_id))
            event_path = root / 'logs/events.jsonl'
            with event_path.open('a') as log:
                log.write(canonical_json({'timestamp': now(), **event}) + '\n')
            if completed:
                (root / 'exit.json').write_text(canonical_json({'timestamp': now(), **event, 'state': state}))
                execution.collect_artifacts(project, run_id)
        except Exception as exc:
            if completed:
                with project_connection(project, write=True) as db:
                    db.execute("UPDATE runs SET state='failed',failure_message=? WHERE id=?", ('Status/artifact publication failed: ' + str(exc), run_id))
            raise
        finally:
            if completed:
                with LIVE_LOCK:
                    LIVE_ATTEMPTS.pop((project.id, run_id), None)
                try:
                    _write_lease(handle, {**lease, 'released': release_on_complete})
                finally:
                    if release_on_complete:
                        _release_slot(handle)

    def prepare():
        current = execution.get_run(project, run_id)
        if current['cancel_requested']:
            cancel_event.set()

    steps = [qe_runner.JobStep(label=step['kind'], command=step['command'], cwd=root, output_path=root / step['output'],
                prepare=prepare, validator=lambda step=step: execution.validate_step(project, run_id, step))
             for step in json.loads(run['specification_json'])]
    try:
        qe_runner.run_chained_jobs(job_label=calculation.label, steps=steps, observer=observer,
            cancel_event=cancel_event, runtime_id=run_id, process_group=True, pass_fds=(handle.fileno(),))
    except Exception as exc:
        observer({'event': 'complete', 'ordinal': 1, 'state': 'failed', 'returncode': None, 'message': str(exc)})
        raise
    return execution.get_run(project, run_id)


def submit_batch(project, calculation_ids, *, owner_id, commands=None):
    """Queue normal attempts and retain the shared slot across their runner threads.

    The driver is process-local, just like single runs. Queued attempts survive
    controller restart, but restarting their dispatch always requires an action.
    """
    handle = _acquire_slot()
    attempts = []
    try:
        for identifier in dict.fromkeys(calculation_ids):
            calculation = get_calculation(project, identifier)
            previous = execution.list_runs(project, identifier)
            latest = previous[-1] if previous else None
            if latest and latest['state'] == 'completed':
                continue
            if latest and latest['state'] == 'running':
                raise ProjectError('A selected calculation still has a running attempt. Reconcile it before retrying.')
            attempt = latest if latest and latest['state'] == 'queued' else execution.create_run(
                project, identifier, expected_revision=calculation.draft_revision, commands=commands)
            attempts.append(attempt['id'])
        if not attempts:
            raise ProjectError('No pending or failed calculations to execute.')
        stop = threading.Event()
        entry = {'run_ids': attempts, 'stop': stop, 'error': None}
        _write_lease(handle, {'project_id': project.id, 'owner_id': owner_id, 'controller': process_identity(os.getpid()),
                              'process': {}, 'released': False})

        def dispatch():
            try:
                for run_id in attempts:
                    if stop.is_set():
                        break
                    try:
                        _launch(project, run_id, handle, release_on_complete=False)
                        context = qe_runner.get_run_context(run_id)
                        context.thread.join()
                        if execution.get_run(project, run_id)['state'] == 'cancelled':
                            stop.set()
                    except Exception as exc:
                        # Preserve the attempt and let the user inspect/restart it.
                        if not stop.is_set():
                            entry['error'] = str(exc)
                        stop.set()
                        break
            finally:
                try:
                    if stop.is_set():
                        for run_id in attempts:
                            if execution.get_run(project, run_id)['state'] == 'queued':
                                cancel_run(project, run_id)
                finally:
                    try:
                        with LIVE_LOCK:
                            still_owned = any((project.id, run_id) in LIVE_ATTEMPTS for run_id in attempts)
                        if not still_owned:
                            _write_lease(handle, {'released': True, 'owner_id': owner_id})
                    finally:
                        if not still_owned:
                            _release_slot(handle)

        entry['thread'] = threading.Thread(target=dispatch, name='qe-batch-' + owner_id, daemon=True)
        with LIVE_LOCK:
            BATCHES[(project.id, owner_id)] = entry
        entry['thread'].start()
        return attempts
    except BaseException:
        if not handle.closed:
            _release_slot(handle)
        raise


def cancel_batch(project, owner_id):
    with LIVE_LOCK:
        batch = BATCHES.get((project.id, owner_id))
        if batch:
            batch['stop'].set()
    if batch:
        for run_id in batch['run_ids']:
            cancel_run(project, run_id)


def cancel_run(project, run_id):
    run = execution.get_run(project, run_id)
    if run['state'] in {'completed', 'failed', 'cancelled'}:
        return run
    with project_connection(project, write=True) as db:
        db.execute('UPDATE runs SET cancel_requested=1 WHERE id=?', (run_id,))
        if run['state'] == 'queued':
            db.execute("UPDATE runs SET state='cancelled',finished_at=? WHERE id=?", (now(), run_id))
            db.execute("UPDATE run_steps SET state='cancelled',finished_at=? WHERE run_id=?", (now(), run_id))
    with LIVE_LOCK:
        live = LIVE_ATTEMPTS.get((project.id, run_id))
        if live:
            live['cancel'].set()
    identity = json.loads(execution.get_run(project, run_id)['process_json'])
    if identity_alive(identity):
        try:
            os.killpg(identity['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass
        def escalate():
            time.sleep(3)
            if identity_alive(identity):
                try:
                    os.killpg(identity['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=escalate, daemon=True).start()
    return execution.get_run(project, run_id)


def reconcile_runs(project):
    """Conservative restart recovery. Never dispatch queued/interrupted attempts."""
    for run in execution.list_runs(project):
        if run['state'] != 'running':
            continue
        with LIVE_LOCK:
            if (project.id, run['id']) in LIVE_ATTEMPTS:
                continue
        process = json.loads(run['process_json'])
        detail = 'A matching QE process is still alive.' if identity_alive(process) else 'Process termination is not conclusively recorded.'
        with project_connection(project, write=True) as db:
            db.execute("UPDATE runs SET state='interrupted',failure_message=? WHERE id=? AND state='running'",
                       ('Live monitoring was lost. ' + detail + ' No retry was launched.', run['id']))
            db.execute("UPDATE run_steps SET state='interrupted' WHERE run_id=? AND state IN ('queued','running')", (run['id'],))
    return execution.list_runs(project)
