import shutil
import sys

from streamlit.testing.v1 import AppTest

from test_calculations import configured_project
from test_calculation_ui import render, button, select
from test_project_execution import isolate_runtime, launch_scf, generated, finish, SCF_SCRIPT, DOS_SCRIPT
from test_freshness import revised
from qe_studio.services.baseline_service import set_active_baseline
from qe_studio.services.project_service import open_project
from qe_studio.services import execution_service as execution, execution_coordinator as coordinator, calculation_service as calculations
from qe_studio.storage.project_repository import project_connection


def test_completed_stale_ui_and_duplicate_current_baseline(configured_project):
    p,b=configured_project;calc,run=launch_scf(p,b)
    new=revised(p,b,ecutwfc=100,ecutrho=800)
    set_active_baseline(open_project(p.root_path),new.id)
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    assert not app.exception
    assert any('Freshness: Stale' in w.value for w in app.warning)
    assert execution.get_run(p,run['id'])['state']=='completed'
    button(app,'Duplicate using current baseline').click().run()
    assert not app.exception
    copy=calculations.get_calculation(p,select(app,'Selected calculation').value)
    assert copy.baseline_id==new.id and copy.id!=calc.id


def test_corrupt_pseudo_history_still_inspectable(configured_project):
    p,b=configured_project;calc,run=launch_scf(p,b)
    set_active_baseline(open_project(p.root_path),b.id)
    with project_connection(p) as db:
        path=db.execute('SELECT relpath FROM pseudo_entries').fetchone()[0]
    (p.root_path/path).write_text('changed bytes')
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    assert not app.exception
    assert any('Integrity problem: Si pseudopotential' in e.value for e in app.error)
    assert any('Freshness: Current' in s.value for s in app.success)
    assert execution.get_run(p,run['id'])['state']=='completed'


def test_missing_restart_does_not_hide_historical_dos_plot(configured_project):
    p,b=configured_project;_,parent=launch_scf(p,b)
    nscf=generated(p,b,'nscf',parent=parent['id'],overrides={'nbnd':8})
    nrun=finish(p,coordinator.submit_calculation(p,nscf.id,expected_revision=1,commands={'pw.x':[sys.executable,'-c',SCF_SCRIPT]}))
    dos=generated(p,b,'dos',parent=nrun['id'])
    run=finish(p,coordinator.submit_calculation(p,dos.id,expected_revision=1,commands={'dos.x':[sys.executable,'-c',DOS_SCRIPT]}))
    set_active_baseline(open_project(p.root_path),b.id)
    shutil.rmtree(execution.parent_restart(p,run['id']))
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    select(app,'Selected calculation').set_value(dos.id).run()
    assert not app.exception and not app.error
    assert len(app.get('plotly_chart'))==1
    assert execution.get_run(p,run['id'])['state']=='completed'
