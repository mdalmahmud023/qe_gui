import sqlite3

import pytest

from test_calculations import configured_project
from qe_studio.services.project_service import open_project
from qe_studio.services.baseline_service import get_baseline
from qe_studio.storage import migrations
from qe_studio.storage.database import connect


def downgrade_to_v4(project):
    with connect(project.root_path/'project.sqlite3') as db:
        db.execute('BEGIN IMMEDIATE')
        for statement in (*migrations.SCHEMA_V6, *migrations.SCHEMA_V5):
            if statement.startswith('CREATE TRIGGER'):
                db.execute('DROP TRIGGER ' + statement.split()[2])
        for table in ('baseline_recommendations','study_result_sources','study_results','study_points','studies'):
            db.execute('DROP TABLE '+table)
        db.execute('DELETE FROM schema_migrations WHERE version>=5')
        db.execute('UPDATE project SET schema_version=4')
        db.execute('PRAGMA user_version=4')


def test_v4_science_survives_upgrade(configured_project):
    project, baseline=configured_project
    marker=(project.root_path/'project.json').read_bytes()
    downgrade_to_v4(project)
    reopened=open_project(project.root_path)
    assert reopened.id==project.id and reopened.schema_version==6
    assert get_baseline(reopened,baseline.id)==baseline
    assert (project.root_path/'project.json').read_bytes()==marker


def test_failed_study_migration_rolls_back(configured_project,monkeypatch):
    project,baseline=configured_project
    downgrade_to_v4(project)
    monkeypatch.setattr(migrations,'SCHEMA_V5',(*migrations.SCHEMA_V5,'INVALID MIGRATION'))
    with pytest.raises(ValueError):
        open_project(project.root_path)
    with connect(project.root_path/'project.sqlite3',read_only=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4
        assert db.execute('SELECT id FROM baselines').fetchone()[0]==baseline.id
        assert db.execute("SELECT name FROM sqlite_master WHERE name='studies'").fetchone() is None
