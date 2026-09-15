"""Full-precision, ordered ASE snapshots and explicitly versioned identities."""
import hashlib
import json

from ase import Atoms
from ase.io.jsonio import encode, object_hook
from ase.spacegroup import Spacegroup
import numpy as np

HASH_VERSION = 1
TYPE_TAG = '__qe_metadata_type__'


def _pack_metadata(value):
    if isinstance(value, Spacegroup):
        return {TYPE_TAG: 'spacegroup', 'number': value.no, 'setting': value.setting}
    if isinstance(value, tuple):
        return {TYPE_TAG: 'tuple', 'items': [_pack_metadata(item) for item in value]}
    if isinstance(value, dict):
        if TYPE_TAG in value or any(not isinstance(key, str) for key in value):
            raise ValueError('Structure metadata needs string keys without reserved type tags.')
        return {key: _pack_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_pack_metadata(item) for item in value]
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise ValueError('Object-valued metadata arrays are not supported.')
        return value
    if value is None or isinstance(value, (str, int, float, bool, np.generic)):
        return value
    raise ValueError(f'Unsupported structure metadata type: {type(value).__name__}')


def _unpack_metadata(value):
    if isinstance(value, dict):
        if value.get(TYPE_TAG) == 'spacegroup':
            return Spacegroup(value['number'], setting=value['setting'])
        if value.get(TYPE_TAG) == 'tuple':
            return tuple(_unpack_metadata(item) for item in value['items'])
        return {key: _unpack_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack_metadata(item) for item in value]
    return value


def canonical_json(value) -> str:
    # ASE encodes ndarray dtypes/shapes and supported constraints. Sorting makes
    # the encoding deterministic without rounding positions or sorting sites.
    return json.dumps(json.loads(encode(value)), sort_keys=True, separators=(',', ':'), allow_nan=False)


def snapshot_bytes(atoms: Atoms) -> bytes:
    if not len(atoms) or atoms.cell.rank != 3 or atoms.cell.volume <= 1e-8:
        raise ValueError('A structure needs atoms and a nondegenerate full lattice.')
    if not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all():
        raise ValueError('Structure coordinates must be finite.')
    try:
        data = atoms.todict()
        if 'info' in data:
            data['info'] = _pack_metadata(data['info'])
        return canonical_json({'hash_version': HASH_VERSION, 'atoms': data}).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Structure has unsupported or non-finite metadata: {exc}') from exc


def atoms_from_snapshot(payload: bytes) -> Atoms:
    # ASE's convenience decode() coerces numeric string dictionary keys to ints.
    # Use its typed object hook directly to preserve CIF occupancy metadata keys.
    data = json.loads(payload.decode('utf-8'), object_hook=object_hook)
    if data.get('hash_version') != HASH_VERSION:
        raise ValueError('Unsupported structure snapshot version.')
    atom_data = data['atoms']
    if 'info' in atom_data:
        atom_data['info'] = _unpack_metadata(atom_data['info'])
    atoms = Atoms.fromdict(atom_data)
    # Reject malformed payloads and unsupported constraints rather than dropping them.
    if snapshot_bytes(atoms) != payload:
        raise ValueError('Structure snapshot is not in the canonical format.')
    return atoms


def structure_hash(atoms: Atoms) -> str:
    return hashlib.sha256(snapshot_bytes(atoms)).hexdigest()
