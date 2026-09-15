import pytest

from test_calculations import configured_project
from qe_studio.services.studies import study_service as studies
from qe_studio.services.project_service import open_project
from qe_studio.storage import migrations
from qe_studio.storage.database import connect


def downgrade_to_v5(project):
    with connect(project.root_path/'project.sqlite3') as db:
        db.execute('BEGIN IMMEDIATE')
        for statement in migrations.SCHEMA_V6:
            if statement.startswith('CREATE TRIGGER'):
                db.execute('DROP TRIGGER ' + statement.split()[2])
        db.execute('DROP TABLE baseline_recommendations')
        db.execute('DELETE FROM schema_migrations WHERE version=6')
        db.execute('UPDATE project SET schema_version=5')
        db.execute('PRAGMA user_version=5')


def test_upgrade_preserves_study_results_and_source_records(configured_project):
    p,b=configured_project
    study=studies.create_study(p,'cutoff',b.id,{'ecutwfc_values_ry':[40,50,60]})
    studies.generate_study(p,study['id'],expected_revision=1)
    result=studies.refresh_results(p,study['id'])
    before=(p.root_path/result['payload_relpath']).read_bytes()
    with connect(p.root_path/'project.sqlite3',read_only=True) as db:
        sources=[dict(r) for r in db.execute('SELECT * FROM study_result_sources')]
    downgrade_to_v5(p)
    reopened=open_project(p.root_path)
    assert reopened.schema_version==6 and reopened.id==p.id
    assert studies.get_result(reopened,result['id'])==result
    assert (p.root_path/result['payload_relpath']).read_bytes()==before
    with connect(p.root_path/'project.sqlite3',read_only=True) as db:
        assert [dict(r) for r in db.execute('SELECT * FROM study_result_sources')]==sources


def test_recommendation_migration_failure_rolls_back(configured_project,monkeypatch):
    p,b=configured_project
    downgrade_to_v5(p)
    monkeypatch.setattr(migrations,'SCHEMA_V6',(*migrations.SCHEMA_V6,'INVALID SQL'))
    with pytest.raises(ValueError):open_project(p.root_path)
    with connect(p.root_path/'project.sqlite3',read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5
        assert db.execute("SELECT name FROM sqlite_master WHERE name='baseline_recommendations'").fetchone() is None
        assert db.execute('SELECT id FROM baselines').fetchone()[0]==b.id
