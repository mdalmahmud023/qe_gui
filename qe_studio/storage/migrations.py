"""Versioned project identity, scientific definition, and execution migrations."""
from __future__ import annotations

import hashlib
import sqlite3

from ..core.project_models import PROJECT_SCHEMA_VERSION, ProjectError


SCHEMA_V1 = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        checksum TEXT NOT NULL
    )""",
    """CREATE TABLE project (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        id TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version > 0)
    )""",
)
SCHEMA_V1_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V1).encode()).hexdigest()


SCHEMA_V2 = (
    """CREATE TABLE structures (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, name TEXT NOT NULL,
        parent_structure_id TEXT REFERENCES structures(id), operation TEXT NOT NULL,
        operation_json TEXT NOT NULL, source_json TEXT NOT NULL,
        snapshot_relpath TEXT NOT NULL UNIQUE, snapshot_sha256 TEXT NOT NULL,
        structure_hash TEXT NOT NULL, hash_version INTEGER NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE pseudo_sets (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE, settings_json TEXT NOT NULL,
        manifest_relpath TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL,
        pseudo_set_hash TEXT NOT NULL UNIQUE, entry_count INTEGER NOT NULL CHECK(entry_count > 0), created_at TEXT NOT NULL
    )""",
    """CREATE TABLE pseudo_entries (
        pseudo_set_id TEXT NOT NULL REFERENCES pseudo_sets(id), species TEXT NOT NULL,
        filename TEXT NOT NULL, relpath TEXT NOT NULL, sha256 TEXT NOT NULL,
        metadata_json TEXT NOT NULL, PRIMARY KEY(pseudo_set_id, species)
    )""",
    """CREATE TABLE baselines (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL UNIQUE,
        structure_id TEXT NOT NULL REFERENCES structures(id),
        pseudo_set_id TEXT NOT NULL REFERENCES pseudo_sets(id),
        previous_baseline_id TEXT REFERENCES baselines(id),
        settings_json TEXT NOT NULL, rendered_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "ALTER TABLE project ADD COLUMN active_structure_id TEXT REFERENCES structures(id)",
    "ALTER TABLE project ADD COLUMN active_baseline_id TEXT REFERENCES baselines(id)",
    """CREATE TRIGGER pseudo_entries_sealed BEFORE INSERT ON pseudo_entries
        WHEN (SELECT count(*) FROM pseudo_entries WHERE pseudo_set_id=NEW.pseudo_set_id)
          >= (SELECT entry_count FROM pseudo_sets WHERE id=NEW.pseudo_set_id)
        BEGIN SELECT RAISE(ABORT, 'Pseudopotential set is immutable'); END""",
) + tuple(
    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'Scientific records are immutable'); END"
    for table in ("structures", "pseudo_sets", "pseudo_entries", "baselines")
    for action in ("UPDATE", "DELETE")
)
SCHEMA_V2_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V2).encode()).hexdigest()


SCHEMA_V3 = (
    """CREATE TABLE calculations (
        id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, display_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('scf','nscf','relax','vc-relax','bands','dos')),
        origin TEXT NOT NULL CHECK(origin IN ('manual','workflow_generated','study_point')),
        baseline_id TEXT NOT NULL REFERENCES baselines(id), overrides_json TEXT NOT NULL,
        resolved_request_json TEXT NOT NULL, draft_revision INTEGER NOT NULL DEFAULT 1 CHECK(draft_revision>0),
        generated_revision INTEGER, definition_hash TEXT, input_relpath TEXT,
        input_manifest_json TEXT NOT NULL DEFAULT '{}', generator_json TEXT NOT NULL DEFAULT '{}',
        frozen_at TEXT, duplicated_from_id TEXT REFERENCES calculations(id),
        parent_run_id TEXT, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
        UNIQUE(kind,sequence), CHECK(generated_revision IS NULL OR generated_revision<=draft_revision)
    )""",
    """CREATE TRIGGER calculations_identity BEFORE UPDATE OF id,kind,sequence,origin,created_at ON calculations
        BEGIN SELECT RAISE(ABORT, 'Calculation identity is immutable'); END""",
    """CREATE TRIGGER calculations_frozen BEFORE UPDATE OF baseline_id,overrides_json,resolved_request_json,
        draft_revision,generated_revision,definition_hash,input_relpath,input_manifest_json,generator_json,parent_run_id ON calculations
        WHEN OLD.frozen_at IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'Duplicate the frozen calculation before editing'); END""",
    """CREATE TRIGGER calculations_no_thaw BEFORE UPDATE OF frozen_at ON calculations
        WHEN OLD.frozen_at IS NOT NULL AND NEW.frozen_at IS NOT OLD.frozen_at
        BEGIN SELECT RAISE(ABORT, 'Calculations cannot be unfrozen'); END""",
    """CREATE TRIGGER calculations_no_delete BEFORE DELETE ON calculations
        BEGIN SELECT RAISE(ABORT, 'Calculation history is retained'); END""",
)
SCHEMA_V3_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V3).encode()).hexdigest()


SCHEMA_V4 = (
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, calculation_id TEXT NOT NULL REFERENCES calculations(id), attempt INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed','cancelled','interrupted')),
        work_relpath TEXT NOT NULL UNIQUE, specification_json TEXT NOT NULL, frozen_definition_json TEXT NOT NULL,
        runtime_id TEXT, controller_json TEXT NOT NULL DEFAULT '{}', process_json TEXT NOT NULL DEFAULT '{}',
        cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        exit_code INTEGER, failure_message TEXT, scientific_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(calculation_id,attempt)
    )""",
    """CREATE TABLE run_steps (
        run_id TEXT NOT NULL REFERENCES runs(id), ordinal INTEGER NOT NULL,
        kind TEXT NOT NULL, command_json TEXT NOT NULL, cwd_relpath TEXT NOT NULL, output_relpath TEXT NOT NULL,
        state TEXT NOT NULL, started_at TEXT, finished_at TEXT, exit_code INTEGER, failure_message TEXT,
        PRIMARY KEY(run_id,ordinal)
    )""",
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, calculation_id TEXT NOT NULL REFERENCES calculations(id), run_id TEXT NOT NULL REFERENCES runs(id),
        role TEXT NOT NULL, relpath TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(run_id,role,relpath)
    )""",
    """CREATE TRIGGER runs_require_input BEFORE INSERT ON runs
        WHEN NOT EXISTS (SELECT 1 FROM calculations WHERE id=NEW.calculation_id
            AND generated_revision=draft_revision AND input_relpath IS NOT NULL AND definition_hash IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'Current generated input is required'); END""",
    """CREATE TRIGGER runs_freeze AFTER INSERT ON runs
        BEGIN UPDATE calculations SET frozen_at=COALESCE(frozen_at,NEW.created_at) WHERE id=NEW.calculation_id; END""",
    """CREATE TRIGGER runs_definition BEFORE UPDATE OF id,calculation_id,attempt,work_relpath,specification_json,frozen_definition_json ON runs
        BEGIN SELECT RAISE(ABORT, 'Run definition is immutable'); END""",
    """CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
        BEGIN SELECT RAISE(ABORT, 'Run history is retained'); END""",
    """CREATE TRIGGER run_steps_definition BEFORE UPDATE OF run_id,ordinal,kind,command_json,cwd_relpath,output_relpath ON run_steps
        BEGIN SELECT RAISE(ABORT, 'Run step definition is immutable'); END""",
    """CREATE TRIGGER run_steps_no_delete BEFORE DELETE ON run_steps
        BEGIN SELECT RAISE(ABORT, 'Run steps are retained'); END""",
    """CREATE TRIGGER calculations_parent_insert BEFORE INSERT ON calculations
        WHEN NEW.parent_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs WHERE id=NEW.parent_run_id)
        BEGIN SELECT RAISE(ABORT, 'Parent run must belong to this project'); END""",
    """CREATE TRIGGER calculations_parent_update BEFORE UPDATE OF parent_run_id ON calculations
        WHEN NEW.parent_run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs WHERE id=NEW.parent_run_id)
        BEGIN SELECT RAISE(ABORT, 'Parent run must belong to this project'); END""",
    "CREATE INDEX runs_state ON runs(state)",
    "CREATE INDEX artifacts_run ON artifacts(run_id,role)",
) + tuple(
    f"CREATE TRIGGER artifacts_immutable_{action.lower()} BEFORE {action} ON artifacts "
    "BEGIN SELECT RAISE(ABORT, 'Artifacts are immutable records'); END"
    for action in ('UPDATE','DELETE')
)
SCHEMA_V4_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V4).encode()).hexdigest()


SCHEMA_V5 = (
    """CREATE TABLE studies (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('cutoff','kpoint','eos')),
        sequence INTEGER NOT NULL, display_name TEXT NOT NULL, baseline_id TEXT NOT NULL REFERENCES baselines(id),
        spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, draft_revision INTEGER NOT NULL DEFAULT 1,
        state TEXT NOT NULL CHECK(state IN ('draft','generating','generated')), manifest_relpath TEXT,
        duplicated_from_id TEXT REFERENCES studies(id), created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
        UNIQUE(kind,sequence)
    )""",
    """CREATE TABLE study_points (
        study_id TEXT NOT NULL REFERENCES studies(id), ordinal INTEGER NOT NULL,
        point_key TEXT NOT NULL, calculation_id TEXT NOT NULL UNIQUE REFERENCES calculations(id),
        values_json TEXT NOT NULL, PRIMARY KEY(study_id,ordinal), UNIQUE(study_id,point_key)
    )""",
    """CREATE TABLE study_results (
        id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id), revision INTEGER NOT NULL,
        summary_json TEXT NOT NULL, versions_json TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
        payload_relpath TEXT NOT NULL UNIQUE, payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(study_id,revision)
    )""",
    """CREATE TABLE study_result_sources (
        result_id TEXT NOT NULL REFERENCES study_results(id), study_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL, run_id TEXT REFERENCES runs(id), source_json TEXT NOT NULL,
        PRIMARY KEY(result_id,ordinal), FOREIGN KEY(study_id,ordinal) REFERENCES study_points(study_id,ordinal)
    )""",
    """CREATE TRIGGER studies_identity BEFORE UPDATE OF id,kind,sequence,duplicated_from_id,created_at ON studies
        BEGIN SELECT RAISE(ABORT, 'Study identity is immutable'); END""",
    """CREATE TRIGGER studies_frozen BEFORE UPDATE OF baseline_id,spec_json,spec_hash,draft_revision ON studies
        WHEN OLD.state!='draft' BEGIN SELECT RAISE(ABORT, 'Duplicate the generated study to change its specification'); END""",
    """CREATE TRIGGER studies_state BEFORE UPDATE OF state ON studies
        WHEN NOT (NEW.state=OLD.state OR (OLD.state='draft' AND NEW.state='generating') OR
            (OLD.state='generating' AND NEW.state='generated'))
        BEGIN SELECT RAISE(ABORT, 'Study generation cannot be undone'); END""",
    """CREATE TRIGGER studies_no_delete BEFORE DELETE ON studies
        BEGIN SELECT RAISE(ABORT, 'Study history is retained'); END""",
    """CREATE TRIGGER study_points_insert BEFORE INSERT ON study_points
        WHEN (SELECT state FROM studies WHERE id=NEW.study_id)!='generating' OR NOT EXISTS
            (SELECT 1 FROM calculations WHERE id=NEW.calculation_id AND kind='scf' AND origin='study_point' AND frozen_at IS NULL)
        BEGIN SELECT RAISE(ABORT, 'Points require a generating study and a study-point SCF'); END""",
    """CREATE TRIGGER study_calculations_spec BEFORE UPDATE OF baseline_id,overrides_json,draft_revision,parent_run_id ON calculations
        WHEN EXISTS(SELECT 1 FROM study_points WHERE calculation_id=OLD.id)
        BEGIN SELECT RAISE(ABORT, 'Study point definitions are immutable; duplicate the study'); END""",
    """CREATE TRIGGER study_result_source_owner BEFORE INSERT ON study_result_sources
        WHEN NOT EXISTS(SELECT 1 FROM study_results WHERE id=NEW.result_id AND study_id=NEW.study_id)
          OR (NEW.run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM runs JOIN study_points
              ON study_points.calculation_id=runs.calculation_id WHERE runs.id=NEW.run_id
              AND study_points.study_id=NEW.study_id AND study_points.ordinal=NEW.ordinal))
        BEGIN SELECT RAISE(ABORT, 'Result sources must belong to the selected study point'); END""",
    "CREATE INDEX study_results_owner ON study_results(study_id,revision)",
) + tuple(
    f"CREATE TRIGGER {table}_immutable_{action.lower()} BEFORE {action} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'Study history is immutable'); END"
    for table in ('study_points', 'study_results', 'study_result_sources') for action in ('UPDATE', 'DELETE')
)
SCHEMA_V5_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V5).encode()).hexdigest()


SCHEMA_V6 = (
    """CREATE TABLE baseline_recommendations (
        baseline_id TEXT NOT NULL REFERENCES baselines(id), study_result_id TEXT NOT NULL REFERENCES study_results(id),
        fields_json TEXT NOT NULL, policy_version INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(baseline_id,study_result_id)
    )""",
) + tuple(
    f"CREATE TRIGGER baseline_recommendations_immutable_{action.lower()} BEFORE {action} ON baseline_recommendations "
    "BEGIN SELECT RAISE(ABORT, 'Recommendation provenance is immutable'); END"
    for action in ('UPDATE','DELETE')
)
SCHEMA_V6_CHECKSUM = hashlib.sha256("\n".join(SCHEMA_V6).encode()).hexdigest()


def _apply_v6(connection, timestamp):
    for statement in SCHEMA_V6:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (6, ?, ?)", (timestamp, SCHEMA_V6_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=6")
    connection.execute("PRAGMA user_version=6")


def _apply_v5(connection, timestamp):
    for statement in SCHEMA_V5:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (5, ?, ?)", (timestamp, SCHEMA_V5_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=5")
    connection.execute("PRAGMA user_version=5")


def _apply_v4(connection, timestamp):
    for statement in SCHEMA_V4:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (4, ?, ?)", (timestamp, SCHEMA_V4_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=4")
    connection.execute("PRAGMA user_version=4")


def _apply_v3(connection, timestamp):
    for statement in SCHEMA_V3:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (3, ?, ?)", (timestamp, SCHEMA_V3_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=3")
    connection.execute("PRAGMA user_version=3")


def _apply_v2(connection, timestamp):
    for statement in SCHEMA_V2:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (2, ?, ?)", (timestamp, SCHEMA_V2_CHECKSUM))
    connection.execute("UPDATE project SET schema_version=2")
    connection.execute("PRAGMA user_version=2")


def initialize_project_schema(connection: sqlite3.Connection, timestamp: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    for statement in SCHEMA_V1:
        connection.execute(statement)
    connection.execute("INSERT INTO schema_migrations VALUES (1, ?, ?)", (timestamp, SCHEMA_V1_CHECKSUM))
    _apply_v2(connection, timestamp)
    _apply_v3(connection, timestamp)
    _apply_v4(connection, timestamp)

    _apply_v5(connection, timestamp)
    _apply_v6(connection, timestamp)


def migrate_project_schema(connection: sqlite3.Connection, timestamp: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    validate_project_schema(connection)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
        _apply_v2(connection, timestamp)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 2:
        _apply_v3(connection, timestamp)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 3:
        _apply_v4(connection, timestamp)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 4:
        _apply_v5(connection, timestamp)
    if connection.execute("PRAGMA user_version").fetchone()[0] == 5:
        _apply_v6(connection, timestamp)


def validate_project_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectError(f"Project schema {version} is newer than supported schema {PROJECT_SCHEMA_VERSION}.")
    if version not in (1, 2, 3, 4, 5, 6):
        raise ProjectError(f"Unsupported project schema {version}.")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ProjectError("The project database failed its integrity check.")
    migrations = connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    expected = [(1, SCHEMA_V1_CHECKSUM)] + ([(2, SCHEMA_V2_CHECKSUM)] if version >= 2 else [])
    if version >= 3:
        expected.append((3, SCHEMA_V3_CHECKSUM))
    if version >= 4:
        expected.append((4, SCHEMA_V4_CHECKSUM))
    if version >= 5:
        expected.append((5, SCHEMA_V5_CHECKSUM))
    if version >= 6:
        expected.append((6, SCHEMA_V6_CHECKSUM))
    if [(row["version"], row["checksum"]) for row in migrations] != expected:
        raise ProjectError("The project migration history is missing or inconsistent.")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected_tables = {"project", "schema_migrations"}
    if version >= 2:
        expected_tables.update({"structures", "pseudo_sets", "pseudo_entries", "baselines"})
        expected_triggers = {f"{table}_immutable_{action}" for table in ("structures", "pseudo_sets", "pseudo_entries", "baselines") for action in ("update", "delete")}
        expected_triggers.add('pseudo_entries_sealed')
        if version >= 3:
            expected_tables.add('calculations')
            expected_triggers.update({'calculations_identity', 'calculations_frozen', 'calculations_no_thaw', 'calculations_no_delete'})
        if version >= 4:
            expected_tables.update({'runs','run_steps','artifacts'})
            expected_triggers.update({'runs_require_input','runs_freeze','runs_definition','runs_no_delete','run_steps_definition','run_steps_no_delete','calculations_parent_insert','calculations_parent_update','artifacts_immutable_update','artifacts_immutable_delete'})
        if version >= 5:
            expected_tables.update({'studies','study_points','study_results','study_result_sources'})
            expected_triggers.update({'studies_identity','studies_frozen','studies_state','studies_no_delete',
                'study_points_insert','study_calculations_spec','study_result_source_owner'})
            expected_triggers.update(f'{table}_immutable_{action}' for table in ('study_points','study_results','study_result_sources') for action in ('update','delete'))
        if version >= 6:
            expected_tables.add('baseline_recommendations')
            expected_triggers.update({'baseline_recommendations_immutable_update','baseline_recommendations_immutable_delete'})
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if triggers != expected_triggers:
            raise ProjectError("The project's scientific immutability constraints are missing.")
    if tables != expected_tables:
        raise ProjectError("The project database does not match the supported schema.")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ProjectError("The project contains invalid record references.")
