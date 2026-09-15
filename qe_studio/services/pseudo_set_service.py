"""Freeze selected UPF bytes independently of the reusable download cache."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from ase.data import atomic_numbers

from ..core.project_models import ProjectError, PseudopotentialSet
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import canonical_json
from ..storage.project_repository import allocate, project_connection, record, records, staged_payload, touch_project


def download_cache_root():
    base = os.environ.get('QE_STUDIO_CACHE_DIR')
    if base:
        return Path(base).expanduser() / 'pseudopotentials'
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'qe-studio/pseudopotentials'


def list_pseudo_sets(project):
    return records(project, 'pseudo_sets', PseudopotentialSet)


def get_pseudo_set(project, identifier):
    return record(project, 'pseudo_sets', identifier, PseudopotentialSet)


def pseudo_set_hash(mapping):
    return hashlib.sha256(canonical_json({'hash_version': 1, 'species_sha256': mapping}).encode()).hexdigest()


def commit_pseudo_set(project, resolved_entries, *, settings=None):
    if not resolved_entries:
        raise ProjectError('Resolve pseudopotentials before committing a set.')
    with staged_payload(project) as stage:
        (stage / 'files').mkdir()
        entries = []
        for species, supplied in sorted(resolved_entries.items()):
            if species not in atomic_numbers or species == 'X':
                raise ProjectError(f'Invalid pseudopotential species: {species}')
            payload = Path(supplied['local_path']).read_bytes()
            if not payload:
                raise ProjectError(f'Pseudopotential for {species} is empty.')
            digest = hashlib.sha256(payload).hexdigest()
            # Species-based filenames avoid collisions between identically named downloads.
            filename = species + '.UPF'
            (stage / 'files' / filename).write_bytes(payload)
            metadata = {key: value for key, value in supplied.items() if key not in {'local_path', 'filename'}}
            metadata['original_filename'] = supplied.get('filename', Path(supplied['local_path']).name)
            entries.append(dict(species=species, filename=filename, sha256=digest, metadata=metadata))
        digest = pseudo_set_hash({entry['species']: entry['sha256'] for entry in entries})
        with project_connection(project, write=True) as db:
            old = db.execute('SELECT * FROM pseudo_sets WHERE pseudo_set_hash=?', (digest,)).fetchone()
            if old:
                # Deduplication cannot hide damage to an already committed payload.
                existing = PseudopotentialSet(**dict(old))
                load_pseudo_entries(project, existing.id)
                return existing
            sequence, final = allocate(db, project, 'pseudo_sets', 'pseudopotentials', 'set')
            identifier = str(uuid4())
            for entry in entries:
                entry['relpath'] = (final / 'files' / entry['filename']).relative_to(project.root_path).as_posix()
            manifest = canonical_json({'version': 1, 'id': identifier, 'pseudo_set_hash': digest, 'settings': settings or {}, 'entries': entries}).encode()
            (stage / 'manifest.json').write_bytes(manifest)
            entry = PseudopotentialSet(identifier, sequence, canonical_json(settings or {}),
                                      (final / 'manifest.json').relative_to(project.root_path).as_posix(),
                                      hashlib.sha256(manifest).hexdigest(), digest, len(entries), datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO pseudo_sets VALUES (?,?,?,?,?,?,?,?)', tuple(entry.__dict__.values()))
            db.executemany('INSERT INTO pseudo_entries VALUES (?,?,?,?,?,?)',
                           [(identifier, e['species'], e['filename'], e['relpath'], e['sha256'], canonical_json(e['metadata'])) for e in entries])
            stage.rename(final)
            touch_project(db, project)
    return entry


def load_pseudo_entries(project, identifier):
    entry = get_pseudo_set(project, identifier)
    paths = ProjectPaths(project.root_path)
    if hashlib.sha256(paths.resolve_relative(entry.manifest_relpath).read_bytes()).hexdigest() != entry.manifest_sha256:
        raise ProjectError('Pseudopotential manifest failed its integrity check.')
    with project_connection(project) as db:
        rows = db.execute('SELECT * FROM pseudo_entries WHERE pseudo_set_id=? ORDER BY species', (identifier,)).fetchall()
    result = {}
    hashes = {}
    for row in rows:
        path = paths.resolve_relative(row['relpath'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ProjectError(f"Committed UPF content changed for {row['species']}.")
        hashes[row['species']] = row['sha256']
        result[row['species']] = {**json.loads(row['metadata_json']), 'filename': row['filename'], 'local_path': str(path), 'sha256': row['sha256']}
    if len(result) != entry.entry_count or pseudo_set_hash(hashes) != entry.pseudo_set_hash:
        raise ProjectError('Pseudopotential mapping failed its integrity check.')
    return result
