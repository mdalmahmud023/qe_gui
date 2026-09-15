import json

from streamlit.testing.v1 import AppTest
from test_calculations import configured_project
from qe_studio.services import calculation_service as calculations
from qe_studio.core.project_session import widget_key


def render(root):
    from qe_studio.services.project_service import open_project
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages.ui_calculations import render_page
    render_page(build_project_context(project=open_project(root)))


def button(app,label):return next(item for item in app.button if item.label==label)
def select(app,label):return next(item for item in app.selectbox if item.label==label)
def area(app,label):return next(item for item in app.text_area if item.label==label)


def test_create_edit_regenerate_duplicate_and_reopen(configured_project):
    p,b=configured_project
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=15).run()
    button(app,'Create Calculation').click().run()
    assert not app.exception
    c,=calculations.list_calculations(p)
    button(app,'Generate Input').click().run()
    assert not app.exception and not app.error
    area(app,'Calculation overrides (JSON)').set_value('{"ecutwfc":70,"ecutrho":560}')
    button(app,'Save Draft').click().run()
    assert not app.exception and not app.error
    assert any('needs regeneration' in warning.value for warning in app.warning)
    button(app,'Generate Input').click().run()
    assert not app.exception and not app.error
    assert calculations.get_calculation(p,c.id).generated_revision==2
    assert len(calculations.list_calculations(p))==1
    button(app,'Duplicate Calculation').click().run()
    assert not app.exception
    assert len(calculations.list_calculations(p))==2, ([e.value for e in app.error], [(b.label,b.value) for b in app.button])
    app=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=15).run()
    assert not app.exception
    select(app,'Selected calculation').set_value(c.id).run()
    assert not app.exception
    assert json.loads(area(app,'Calculation overrides (JSON)').value)['ecutwfc']==70


def test_two_tabs_conflicting_scientific_edits(configured_project):
    p,b=configured_project
    c=calculations.create_calculation(p,'scf',b.id)
    first=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=15).run()
    second=AppTest.from_function(render,args=(str(p.root_path),),default_timeout=15).run()
    area(first,'Calculation overrides (JSON)').set_value('{"ecutwfc":60}')
    button(first,'Save Draft').click().run()
    area(second,'Calculation overrides (JSON)').set_value('{"ecutwfc":80}')
    button(second,'Save Draft').click().run()
    assert not second.exception
    assert any('another tab' in error.value for error in second.error)
    assert json.loads(calculations.get_calculation(p,c.id).overrides_json)['ecutwfc']==60
    button(second,'Reload Editor').click().run()
    assert json.loads(area(second,'Calculation overrides (JSON)').value)['ecutwfc']==60
