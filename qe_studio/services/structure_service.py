"""Immutable project structures, explicit transformations, and active selection."""
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

import ase
from ase import Atoms
from ase.io import write
import spglib
import numpy as np

from ..core.project_models import ProjectError, StructureRecord
from ..core.project_paths import ProjectPaths
from ..core.structure_identity import HASH_VERSION, atoms_from_snapshot, canonical_json, snapshot_bytes
from ..storage.project_repository import allocate, project_connection, record, records, staged_payload, touch_project
from .project_service import open_project
from .structure_tools import make_primitive_atoms, make_conventional_atoms


def list_structures(project):
    return records(project, 'structures', StructureRecord)


def get_structure(project, identifier):
    return record(project, 'structures', identifier, StructureRecord)


def load_structure(project, identifier):
    entry = get_structure(project, identifier)
    payload = ProjectPaths(project.root_path).resolve_relative(entry.snapshot_relpath).read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry.snapshot_sha256 or entry.structure_hash != entry.snapshot_sha256:
        raise ProjectError('Structure snapshot content does not match its committed hash.')
    if entry.hash_version != HASH_VERSION:
        raise ProjectError('Unsupported structure hash version.')
    return atoms_from_snapshot(payload)


def commit_structure(project, atoms, *, name, source=None, parent_id=None, operation='import',
                     parameters=None, original_bytes=None, original_filename=None, identifier=None):
    """Commit a snapshot without adopting it. A preview token makes Apply idempotent."""
    if not str(name).strip():
        raise ProjectError('Structure name is required.')
    if parent_id:
        get_structure(project, parent_id)
    payload = snapshot_bytes(atoms)
    atoms_from_snapshot(payload)  # Validate reload support before publishing anything.
    digest = hashlib.sha256(payload).hexdigest()
    identifier = identifier or str(uuid4())
    source = dict(source or {})
    if original_bytes is not None:
        from pathlib import Path
        suffix = Path(original_filename or '').suffix.lower()
        source_file = 'source' + (suffix if suffix in {'.cif', '.vasp', '.xsf', '.xyz', '.extxyz'} else '.bin')
        source.update(original_filename=original_filename, original_file=source_file,
                      original_sha256=hashlib.sha256(original_bytes).hexdigest())
    source_json = canonical_json(source)
    operation_json = canonical_json(parameters or {})
    with staged_payload(project) as stage:
        (stage / 'atoms.json').write_bytes(payload)
        # CIF is geometry interchange only; the snapshot retains arrays/constraints.
        interchange = Atoms(numbers=atoms.numbers, positions=atoms.positions, cell=atoms.cell, pbc=atoms.pbc)
        write(stage / 'structure.cif', interchange, format='cif')
        if original_bytes is not None:
            (stage / source_file).write_bytes(original_bytes)
        with project_connection(project, write=True) as db:
            existing = db.execute('SELECT * FROM structures WHERE id=?', (identifier,)).fetchone()
            if existing:
                if (existing['snapshot_sha256'], existing['parent_structure_id'], existing['operation'], existing['operation_json'], existing['source_json']) != (digest, parent_id, operation, operation_json, source_json):
                    raise ProjectError('This preview token was already used for a different structure.')
                return StructureRecord(**dict(existing))
            sequence, final = allocate(db, project, 'structures', 'structures', 'structure')
            entry = StructureRecord(identifier, sequence, str(name).strip(), parent_id, operation, operation_json,
                                    source_json, (final / 'atoms.json').relative_to(project.root_path).as_posix(),
                                    digest, digest, HASH_VERSION, datetime.now(timezone.utc).isoformat())
            (stage / 'metadata.json').write_text(canonical_json(entry.__dict__), encoding='utf-8')
            db.execute('INSERT INTO structures VALUES (' + ','.join('?' for _ in entry.__dict__) + ')', tuple(entry.__dict__.values()))
            stage.rename(final)
            touch_project(db, project)
    return entry


def preview_transformation(project, parent_id, operation, *, repeat=(1, 1, 1), symprec=1e-5):
    """Read the selected frozen parent each time; never repeat an earlier preview."""
    atoms = load_structure(project, parent_id)
    parameters = {'ase_version': ase.__version__, 'spglib_version': spglib.__version__}
    if operation == 'supercell':
        if len(repeat) != 3 or any(int(n) != n or not 1 <= n <= 3 for n in repeat):
            raise ProjectError('Supercell repeats must be integers from 1 to 3.')
        parameters['repeat'] = [int(n) for n in repeat]
        transformed = atoms.repeat(parameters['repeat'])
    elif operation in {'primitive', 'conventional'}:
        # spglib returns geometry without site arrays. Species-uniform properties
        # can be mapped exactly; reject ambiguous site-specific information.
        extras = set(atoms.arrays) - {'numbers', 'positions'}
        source_labels = atoms.arrays.get('spacegroup_kinds')
        occupancy = atoms.info.get('occupancy')
        if source_labels is not None:
            # ASE CIF site labels are source identifiers, not a physical field.
            # Standardized sites receive fresh labels; the parent retains their
            # exact originals. Fractional/disordered occupancy is not safely mapped.
            if occupancy:
                for symbol, label in zip(atoms.get_chemical_symbols(), source_labels):
                    occupied = occupancy.get(str(int(label)), occupancy.get(int(label), {}))
                    if occupied != {symbol: 1.0}:
                        raise ProjectError('Cell standardization cannot safely map partial or disordered site occupancies.')
            extras.discard('spacegroup_kinds')
            parameters['source_site_labels'] = source_labels.tolist()
            parameters['site_label_policy'] = 'regenerate; original labels retained in parent snapshot'
        property_maps = {}
        for name in extras:
            mapping = {}
            for symbol, value in zip(atoms.get_chemical_symbols(), atoms.arrays[name]):
                if symbol in mapping and not np.array_equal(mapping[symbol], value):
                    raise ProjectError('Cell standardization cannot safely map site arrays or constraints. The original structure is preserved.')
                mapping[symbol] = value
            property_maps[name] = mapping
        if atoms.constraints:
            raise ProjectError('Cell standardization cannot safely map site arrays or constraints. The original structure is preserved.')
        parameters.update(symprec=float(symprec), angle_tolerance=-1.0, no_idealize=False)
        transform = make_primitive_atoms if operation == 'primitive' else make_conventional_atoms
        transformed = transform(atoms, symprec=symprec).atoms
        for name, mapping in property_maps.items():
            transformed.new_array(name, np.array([mapping[symbol] for symbol in transformed.get_chemical_symbols()], dtype=atoms.arrays[name].dtype))
        if source_labels is not None:
            transformed.new_array('spacegroup_kinds', np.arange(len(transformed), dtype=int))
            if occupancy:
                transformed.info['occupancy'] = {str(i): {symbol: 1.0} for i, symbol in enumerate(transformed.get_chemical_symbols())}
    else:
        raise ProjectError('Unknown structure transformation.')
    snapshot_bytes(transformed)
    return transformed, parameters


def set_active_structure(project, identifier):
    load_structure(project, identifier)  # Verify before adoption.
    with project_connection(project, write=True) as db:
        row = db.execute('SELECT active_structure_id, active_baseline_id FROM project').fetchone()
        if row['active_structure_id'] == identifier:
            return open_project(project.root_path)
        # A baseline remains a valid historical revision, but cannot remain the
        # active scientific configuration for a different structure.
        touch_project(db, project, expected=True, active_structure_id=identifier, active_baseline_id=None)
    return open_project(project.root_path)
