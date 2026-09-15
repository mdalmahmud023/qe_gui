import json
from pathlib import Path

from ase.build import bulk
import pytest

from qe_studio.services.project_service import create_project, open_project
from qe_studio.services.structure_service import commit_structure
from qe_studio.services.pseudo_set_service import commit_pseudo_set
from qe_studio.services.baseline_service import commit_baseline
from qe_studio.services import calculation_service as calculations
from qe_studio.core.project_models import ProjectConflictError


@pytest.fixture
def configured_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / 'qe_runs'; sentinel.mkdir(); (sentinel / 'keep').write_text('legacy')
    project = create_project(tmp_path, 'Project')
    structure = commit_structure(project, bulk('Si'), name='Si')
    source = tmp_path / 'Si.UPF'; source.write_text('<UPF><PP_HEADER element="Si"/></UPF>')
    pseudo = commit_pseudo_set(project, {'Si': {'local_path': str(source)}})
    baseline = commit_baseline(project, structure.id, pseudo.id, {})
    yield project, baseline
    assert {path.name: path.read_text() for path in sentinel.iterdir()} == {'keep': 'legacy'}


def test_edit_regenerate_keeps_identity_and_previous_inputs(configured_project):
    p, baseline = configured_project
    a = calculations.create_calculation(p, 'scf', baseline.id)
    b = calculations.create_calculation(p, 'scf', baseline.id)
    a = calculations.generate_calculation(p, a.id, expected_revision=1)
    b = calculations.generate_calculation(p, b.id, expected_revision=1)
    old = {name: path.read_bytes() for name, path in calculations.generated_inputs(p, a).items()}
    assert a.input_relpath != b.input_relpath and a.frozen_at is None
    updated = calculations.save_draft(p, a.id, {'ecutwfc': 70, 'ecutrho': 560}, expected_revision=1)
    assert updated.state == 'input_outdated' and updated.generated_revision == 1 and updated.draft_revision == 2
    assert {name: path.read_bytes() for name, path in calculations.generated_inputs(p, updated).items()} == old
    regenerated = calculations.generate_calculation(p, a.id, expected_revision=2)
    assert regenerated.id == a.id and regenerated.state == 'input_generated'
    assert regenerated.input_relpath != a.input_relpath and regenerated.definition_hash != a.definition_hash
    assert {name: path.read_bytes() for name, path in calculations.generated_inputs(p, a).items()} == old
    assert calculations.get_calculation(open_project(p.root_path), a.id) == regenerated
    assert calculations.get_calculation(p, b.id) == b


def test_conflicting_edits_and_failed_generation_preserve_snapshot(configured_project, monkeypatch):
    p, baseline = configured_project
    c = calculations.create_calculation(p, 'scf', baseline.id)
    c = calculations.generate_calculation(p, c.id, expected_revision=1)
    updated = calculations.save_draft(p, c.id, {'ecutwfc': 50}, expected_revision=1)
    with pytest.raises(ProjectConflictError):
        calculations.save_draft(p, c.id, {'ecutwfc': 60}, expected_revision=1)
    def fail(*args, **kwargs):
        raise RuntimeError('generation failed')
    monkeypatch.setattr(calculations, 'generate_qe_input_text', fail)
    with pytest.raises(RuntimeError):
        calculations.generate_calculation(p, c.id, expected_revision=2)
    assert calculations.get_calculation(p, c.id) == updated
    assert calculations.generated_inputs(p, updated)['pw.in'].read_bytes() == calculations.generated_inputs(p, c)['pw.in'].read_bytes()


def test_duplicate_rename_hash_and_project_isolation(configured_project, tmp_path):
    p, baseline = configured_project
    c = calculations.create_calculation(p, 'scf', baseline.id)
    c = calculations.generate_calculation(p, c.id, expected_revision=1)
    renamed = calculations.rename_calculation(p, c.id, 'Renamed', notes='annotation')
    assert renamed.definition_hash == c.definition_hash and renamed.draft_revision == c.draft_revision
    copied = calculations.duplicate_calculation(p, c.id)
    assert copied.id != c.id and copied.duplicated_from_id == c.id and copied.generated_revision is None
    other = create_project(tmp_path, 'Other')
    with pytest.raises(ValueError):
        calculations.get_calculation(other, c.id)
    with pytest.raises(ValueError):
        calculations.create_calculation(other, 'scf', baseline.id)
    with pytest.raises(ValueError):
        calculations.save_draft(p, c.id, {'outdir': str(other.root_path)}, expected_revision=1)
    moved = tmp_path / 'moved'; p.root_path.rename(moved); reopened = open_project(moved)
    c = calculations.get_calculation(reopened, c.id)
    assert calculations.generated_inputs(reopened, c)['pw.in'].is_file()


def test_generation_race_does_not_publish_as_current(configured_project, monkeypatch):
    p, baseline = configured_project
    c = calculations.create_calculation(p, 'scf', baseline.id)
    original = calculations.generate_qe_input_text
    def edit_during_generation(*args, **kwargs):
        result = original(*args, **kwargs)
        calculations.save_draft(p, c.id, {'ecutwfc': 55}, expected_revision=1)
        return result
    monkeypatch.setattr(calculations, 'generate_qe_input_text', edit_during_generation)
    with pytest.raises(ProjectConflictError):
        calculations.generate_calculation(p, c.id, expected_revision=1)
    assert calculations.get_calculation(p, c.id).generated_revision is None
