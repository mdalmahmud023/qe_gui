import json
import io
from pathlib import Path
from types import SimpleNamespace

from ase.build import bulk
from ase.io import write
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from qe_studio.core.project_session import widget_key, activate_context
from qe_studio.services.project_service import create_project, open_project
from qe_studio.services import structure_service as structures, pseudo_set_service as pseudos, baseline_service as baselines


def render_structure(root):
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages.ui_project_structure import render_page
    from qe_studio.services.project_service import open_project
    render_page(build_project_context(project=open_project(root)))


def render_baseline(root):
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages.ui_project_baseline import render_page
    from qe_studio.services.project_service import open_project
    render_page(build_project_context(project=open_project(root)))


def button(app, label):
    return next(item for item in app.button if item.label == label)


def select(app, label):
    return next(item for item in app.selectbox if item.label == label)


def number(app, label):
    return next(item for item in app.number_input if item.label == label)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / 'qe_runs'; sentinel.mkdir(); (sentinel / 'keep').write_bytes(b'legacy data')
    monkeypatch.setenv('QE_STUDIO_CACHE_DIR', str(tmp_path / 'cache'))
    project = create_project(tmp_path, 'Project')
    yield project
    assert {p.name: p.read_bytes() for p in sentinel.iterdir()} == {'keep': b'legacy data'}


def test_build_preview_apply_transform_and_activate(project):
    app = AppTest.from_function(render_structure, args=(str(project.root_path),)).run()
    assert not app.exception
    app.radio[0].set_value('Prototype').run()
    button(app, 'Preview Prototype').click().run()
    assert not app.exception
    assert structures.list_structures(project) == []
    button(app, 'Apply Structure').click().run()
    assert not app.exception
    first, = structures.list_structures(project)
    assert open_project(project.root_path).active_structure_id is None
    button(app, 'Set Active').click().run()
    assert not app.exception
    assert open_project(project.root_path).active_structure_id == first.id
    select(app, 'Operation').set_value('primitive').run()
    assert len(structures.list_structures(project)) == 1
    button(app, 'Apply Transformation').click().run()
    assert not app.exception
    second = structures.list_structures(project)[-1]
    assert second.parent_structure_id == first.id
    assert select(app, 'View structure').value == first.id
    button(app, 'Apply Transformation').click().run()
    assert len(structures.list_structures(project)) == 2
    select(app, 'View structure').set_value(second.id).run()
    select(app, 'Operation').set_value('supercell').run()
    number(app, 'Repeat a').set_value(2).run()
    number(app, 'Repeat b').set_value(2).run()
    assert len(structures.list_structures(project)) == 2
    button(app, 'Apply Transformation').click().run()
    assert not app.exception
    third = structures.list_structures(project)[-1]
    assert third.parent_structure_id == second.id
    assert json.loads(third.operation_json)['repeat'] == [2, 2, 1]
    assert open_project(project.root_path).active_structure_id == first.id
    assert select(app, 'View structure').value == second.id
    select(app, 'View structure').set_value(third.id).run()
    button(app, 'Set Active').click().run()
    assert open_project(project.root_path).active_structure_id == third.id
    select(app, 'View structure').set_value(first.id).run()
    button(app, 'Set Active').click().run()
    assert open_project(project.root_path).active_structure_id == first.id
    assert len(structures.list_structures(project)) == 3


def test_cif_import_ui_preserves_source_and_manual_preview_does_not_commit(project, monkeypatch):
    output = io.BytesIO()
    write(output, bulk('Si', cubic=True), format='cif')
    payload = output.getvalue()
    uploaded = SimpleNamespace(name='silicon.cif', getvalue=lambda: payload)
    monkeypatch.setattr('qe_studio.pages.ui_project_structure.st.file_uploader', lambda *args, **kwargs: uploaded)
    app = AppTest.from_function(render_structure, args=(str(project.root_path),)).run()
    button(app, 'Preview Import').click().run()
    assert not app.exception and not app.error
    assert not structures.list_structures(project)
    button(app, 'Apply Structure').click().run()
    assert not app.exception and not app.error
    entry, = structures.list_structures(project)
    assert (project.root_path / Path(entry.snapshot_relpath).parent / 'source.cif').read_bytes() == payload
    app.radio[0].set_value('Manual builder').run()
    button(app, 'Preview Build').click().run()
    assert not app.exception and not app.error
    assert len(structures.list_structures(project)) == 1
    app.radio[0].set_value('Prototype').run()
    assert 'Apply Structure' not in [item.label for item in app.button]


@pytest.mark.parametrize('family', ['SSSP', 'Fully relativistic SG15'])
def test_resolve_commit_baseline_reopen_without_cache(project, tmp_path, monkeypatch, family):
    structure = structures.commit_structure(project, bulk('Si'), name='Silicon')
    structures.set_active_structure(open_project(project.root_path), structure.id)
    roots = []
    def resolve(atoms, *, cache_root, **kwargs):
        roots.append(cache_root)
        cache_root.mkdir(parents=True, exist_ok=True)
        path = cache_root / 'Si.UPF'; path.write_text('<UPF><PP_HEADER element="Si" has_so="T" relativistic="full"/></UPF>')
        return {'settings': {'functional': 'PBE'}, 'resolved_entries': {'Si': {'local_path': str(path)}}, 'warnings': []}
    monkeypatch.setattr('qe_studio.pages.ui_project_baseline.resolve_sssp_pseudopotentials', resolve)
    monkeypatch.setattr('qe_studio.pages.ui_project_baseline.resolve_fully_relativistic_pseudopotentials', resolve)
    app = AppTest.from_function(render_baseline, args=(str(project.root_path),)).run()
    select(app, 'Download family').set_value(family).run()
    button(app, 'Resolve Pseudopotentials').click().run()
    assert not app.exception and pseudos.list_pseudo_sets(project) == []
    expected = pseudos.download_cache_root() / 'sssp' if family == 'SSSP' else pseudos.download_cache_root()
    assert roots == [expected]
    button(app, 'Commit Pseudopotential Set').click().run()
    assert not app.exception
    assert len(pseudos.list_pseudo_sets(project)) == 1
    number(app, 'Wavefunction cutoff (Ry)').set_value(70)
    number(app, 'Charge density cutoff (Ry)').set_value(560)
    button(app, 'Preview Baseline').click().run()
    assert not app.exception and not app.error
    assert baselines.list_baselines(project) == []
    button(app, 'Commit Baseline Revision').click().run()
    assert not app.exception and not app.error
    first, = baselines.list_baselines(project)
    assert json.loads(first.settings_json)['ecutwfc'] == 70
    button(app, 'Set Active Baseline').click().run()
    assert open_project(project.root_path).active_baseline_id == first.id
    number(app, 'Wavefunction cutoff (Ry)').set_value(80)
    button(app, 'Commit Baseline Revision').click().run()
    assert not app.exception
    second = baselines.list_baselines(project)[-1]
    assert second.previous_baseline_id == first.id
    assert json.loads(baselines.get_baseline(project, first.id).settings_json)['ecutwfc'] == 70
    import shutil
    shutil.rmtree(tmp_path / 'cache')
    restarted = AppTest.from_function(render_baseline, args=(str(project.root_path),)).run()
    assert not restarted.exception and not restarted.error
    assert number(restarted, 'Wavefunction cutoff (Ry)').value == 70


def render_switcher(roots):
    import streamlit as st
    from qe_studio.core.project_session import activate_context, PREFIX
    from qe_studio.services.project_service import open_project
    from qe_studio.core.project_context import build_project_context
    from qe_studio.pages.ui_project_baseline import render_page
    selected = st.selectbox('Project selector', roots, key=PREFIX + 'test_selector')
    project = open_project(selected)
    activate_context(st.session_state, project.id, selected)
    render_page(build_project_context(project=project))


def test_same_tab_switches_reload_exact_baselines_and_drop_foreign_drafts(project, tmp_path):
    other = create_project(tmp_path, 'Project B')
    for current, symbol, cutoff in [(project, 'Si', 70), (other, 'Al', 90)]:
        structure = structures.commit_structure(current, bulk(symbol), name=symbol)
        path = tmp_path / (symbol + '.UPF'); path.write_text(symbol + ' bytes')
        pseudo = pseudos.commit_pseudo_set(current, {symbol: {'local_path': str(path)}})
        baseline = baselines.commit_baseline(current, structure.id, pseudo.id, {'ecutwfc': cutoff, 'ecutrho': 600})
        baselines.set_active_baseline(open_project(current.root_path), baseline.id)
    roots = [str(project.root_path), str(other.root_path)]
    app = AppTest.from_function(render_switcher, args=(roots,)).run()
    assert not app.exception
    number(app, 'Wavefunction cutoff (Ry)').set_value(123).run()
    select(app, 'Project selector').set_value(roots[1]).run()
    assert not app.exception
    assert number(app, 'Wavefunction cutoff (Ry)').value == 90
    assert not any(str(k).startswith('project:' + project.id + ':') for k in app.session_state.filtered_state)
    number(app, 'Wavefunction cutoff (Ry)').set_value(321).run()
    select(app, 'Project selector').set_value(roots[0]).run()
    assert number(app, 'Wavefunction cutoff (Ry)').value == 70
    assert not any(str(k).startswith('project:' + other.id + ':') for k in app.session_state.filtered_state)
    assert open_project(project.root_path).active_baseline_id != open_project(other.root_path).active_baseline_id
