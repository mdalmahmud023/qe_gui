import json

from streamlit.testing.v1 import AppTest

from test_calculations import configured_project
from test_calculation_ui import render as render_calculations, button, select, area
from qe_studio.services.studies import study_service as studies
from qe_studio.services import calculation_service as calculations
from qe_studio.storage.project_repository import project_connection


def render(root):
    from qe_studio.services.project_service import open_project
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages.ui_project_studies import render_page
    render_page(build_project_context(project=open_project(root)))


def test_create_generate_refresh_history_and_duplicate(configured_project):
    p,b = configured_project
    app = AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    button(app,'Create Study').click().run()
    assert not app.exception and not app.error
    button(app,'Generate Study Points').click().run()
    assert not app.exception and not app.error
    study, = studies.list_studies(p)
    assert study['state']=='generated' and len(studies.study_points(p,study['id']))==5
    assert area(app,'Sweep specification (JSON)').disabled
    button(app,'Refresh Study Results').click().run()
    assert not app.exception and not app.error
    button(app,'Refresh Study Results').click().run()
    assert len(studies.list_results(p,study['id']))==2
    assert not app.exception
    button(app,'Duplicate Study').click().run()
    assert not app.exception
    assert len(studies.list_studies(p))==2
    assert not area(app,'Sweep specification (JSON)').disabled


def test_study_calculations_hidden_until_enabled(configured_project):
    p,b = configured_project
    study = studies.create_study(p,'cutoff',b.id,{'ecutwfc_values_ry':[40,50,60]})
    studies.generate_study(p,study['id'],expected_revision=1)
    normal = calculations.create_calculation(p,'scf',b.id)
    app = AppTest.from_function(render_calculations,args=(str(p.root_path),),default_timeout=20).run()
    assert len(select(app,'Selected calculation').options)==1
    assert select(app,'Selected calculation').value==normal.id
    next(c for c in app.checkbox if c.label=='Show study calculations').check().run()
    assert len(select(app,'Selected calculation').options)==4
    member = studies.study_points(p,study['id'])[0]
    select(app,'Selected calculation').set_value(member['calculation_id']).run()
    assert button(app,'Save Draft').disabled
    assert not app.exception


def test_results_browse_without_active_setup_or_pseudo_files(configured_project):
    p,b=configured_project
    study=studies.create_study(p,'kpoint',b.id,studies.DEFAULT_SPECS['kpoint'])
    studies.generate_study(p,study['id'],expected_revision=1)
    studies.refresh_results(p,study['id'])
    with project_connection(p,write=True) as db:
        db.execute('UPDATE project SET active_structure_id=NULL,active_baseline_id=NULL')
        path=db.execute('SELECT relpath FROM pseudo_entries LIMIT 1').fetchone()[0]
    (p.root_path/path).unlink()
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    assert not app.exception and not app.error
    assert len(select(app,'Result revision').options)==1


def test_two_tabs_reject_conflicting_study_draft_edits(configured_project):
    p,b=configured_project
    study=studies.create_study(p,'cutoff',b.id,studies.DEFAULT_SPECS['cutoff'])
    first=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    second=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=20).run()
    area(first,'Sweep specification (JSON)').set_value('{"ecutwfc_values_ry":[40,50,60]}')
    button(first,'Save Study Draft').click().run()
    area(second,'Sweep specification (JSON)').set_value('{"ecutwfc_values_ry":[60,70,80]}')
    button(second,'Save Study Draft').click().run()
    assert not second.exception
    assert any('another tab' in e.value for e in second.error)
    assert studies.get_study(p,study['id'])['draft_revision']==2
    button(second,'Reload Study Editor').click().run()
    assert json.loads(area(second,'Sweep specification (JSON)').value)['ecutwfc_values_ry']==[40,50,60]
