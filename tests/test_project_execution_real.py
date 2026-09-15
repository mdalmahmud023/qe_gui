"""Opt-in real QE smoke: QE_STUDIO_REAL_QE=1 QE_STUDIO_TEST_UPF=/path/to/Si.UPF."""
import os
from pathlib import Path
import shutil

from ase.build import bulk
import pytest

from qe_studio.services.project_service import create_project
from qe_studio.services.structure_service import commit_structure
from qe_studio.services.pseudo_set_service import commit_pseudo_set
from qe_studio.services.baseline_service import commit_baseline
from qe_studio.services import calculation_service as calculations, execution_service as execution, execution_coordinator as coordinator, qe_runner

pytestmark = pytest.mark.skipif(os.environ.get('QE_STUDIO_REAL_QE') != '1', reason='Opt-in real Quantum ESPRESSO integration')


def test_real_scf_nscf_dos_and_bands(tmp_path, monkeypatch):
    source = Path(os.environ.get('QE_STUDIO_TEST_UPF', ''))
    assert source.is_file(), 'Set QE_STUDIO_TEST_UPF to a real silicon UPF.'
    for command in ('pw.x', 'dos.x', 'bands.x'):
        assert shutil.which(command), f'{command} must be installed for the opt-in workflow smoke test.'
    monkeypatch.setenv('QE_STUDIO_WORKSPACE_DIR', str(tmp_path / 'machine'))
    monkeypatch.setenv('OMP_NUM_THREADS', '1')
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '1')
    project = create_project(tmp_path, 'Real QE smoke')
    structure = commit_structure(project, bulk('Si', a=5.43), name='Si')
    pseudo = commit_pseudo_set(project, {'Si': {'local_path': str(source)}})
    baseline = commit_baseline(project, structure.id, pseudo.id, {'ecutwfc': 20, 'ecutrho': 160, 'k_points_grid': [1,1,1], 'conv_thr': 1e-6})
    def execute(kind, parent=None, overrides=None):
        calc = calculations.create_calculation(project, kind, baseline.id, parent_run_id=parent, overrides=overrides)
        calc = calculations.generate_calculation(project, calc.id, expected_revision=1)
        run = coordinator.submit_calculation(project, calc.id, expected_revision=1)
        context = qe_runner.get_run_context(run['id'])
        context.thread.join(timeout=90)
        if context.thread.is_alive():
            coordinator.cancel_run(project, run['id']); context.thread.join(timeout=10)
            pytest.fail('Real QE smoke exceeded 90 seconds for one workflow.')
        completed = execution.get_run(project, run['id'])
        outputs = '\n'.join(path.read_text(errors='replace')[-5000:] for path in (project.root_path / run['work_relpath']).glob('*.out'))
        assert completed['state'] == 'completed', str(completed['failure_message']) + '\n' + outputs
        return completed
    scf = execute('scf')
    parent_tree = execution.parent_restart(project, scf['id'])
    parent_hash = execution.content_hash(parent_tree)
    nscf = execute('nscf', scf['id'], {'nbnd':8, 'k_points_grid':[2,2,2]})
    dos = execute('dos', nscf['id'])
    bands = execute('bands', scf['id'], {'nbnd':8, 'band_path':'0 0 0 3\n0.5 0 0 1'})
    assert execution.content_hash(parent_tree) == parent_hash
    assert [s['kind'] for s in execution.run_steps(project, bands['id'])] == ['bands', 'bands_pp']
    assert (project.root_path / dos['work_relpath'] / 'dos.dat').is_file()
