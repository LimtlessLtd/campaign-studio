"""Read-only access to a Foundry LevelDB folder using only the standard library.

Foundry v11+ stores each world collection (scenes, journal, actors, items...) in a LevelDB folder.
This reads its `.log` and `.ldb`/`.sst` files without opening the database, so it never writes,
never takes Foundry's lock and works while Foundry is running (it sees data as last flushed).
Checksums are not verified: callers treat the result as untrusted and validate every document.
"""

import struct
from pathlib import Path

TABLE_MAGIC = bytes.fromhex('57fb808b247547db')
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_BLOCK_BYTES = 64 * 1024 * 1024
PUT = 1
LOG_BLOCK = 32768


class LevelDBError(ValueError):
    pass


def _varint(data, pos):
    shift = result = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise LevelDBError('The Foundry database is damaged.')


def _snappy(data):
    """Decompress a raw Snappy block (LevelDB's block compression)."""
    length, pos = _varint(data, 0)
    if length > MAX_BLOCK_BYTES:
        raise LevelDBError('The Foundry database has an unexpectedly large block.')
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            size = tag >> 2
            if size >= 60:
                extra = size - 59
                size = int.from_bytes(data[pos : pos + extra], 'little')
                pos += extra
            size += 1
            out += data[pos : pos + size]
            pos += size
            continue
        if kind == 1:
            size = 4 + ((tag >> 2) & 7)
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos : pos + 2], 'little')
            pos += 2
        else:
            size = 1 + (tag >> 2)
            offset = int.from_bytes(data[pos : pos + 4], 'little')
            pos += 4
        if not 0 < offset <= len(out):
            raise LevelDBError('The Foundry database is damaged.')
        # A copy may overlap its own output; repeat the pattern instead of copying byte by byte.
        pattern = out[-offset:]
        out += (pattern * (size // offset + 1))[:size]
        if len(out) > MAX_BLOCK_BYTES:
            raise LevelDBError('The Foundry database has an unexpectedly large block.')
    if len(out) != length:
        raise LevelDBError('The Foundry database is damaged.')
    return bytes(out)


def _block_entries(block):
    restarts = struct.unpack_from('<I', block, len(block) - 4)[0]
    end = len(block) - 4 - 4 * restarts
    if end < 0:
        raise LevelDBError('The Foundry database is damaged.')
    pos = 0
    key = b''
    while pos < end:
        shared, pos = _varint(block, pos)
        unshared, pos = _varint(block, pos)
        size, pos = _varint(block, pos)
        if shared > len(key):
            raise LevelDBError('The Foundry database is damaged.')
        key = key[:shared] + block[pos : pos + unshared]
        pos += unshared
        yield key, block[pos : pos + size]
        pos += size


def _read_block(data, offset, size):
    if offset + size >= len(data):
        raise LevelDBError('The Foundry database is damaged.')
    raw = data[offset : offset + size]
    compression = data[offset + size]
    if compression == 1:
        return _snappy(raw)
    if compression != 0:
        raise LevelDBError('This Foundry database uses an unsupported compression.')
    return raw


def _overlaps(low, high, prefixes):
    """Whether user keys in (low, high] can start with one of the prefixes."""
    for prefix in prefixes:
        if not prefix:
            return True
        end = prefix[:-1] + bytes([prefix[-1] + 1])
        if high >= prefix and (low is None or low < end):
            return True
    return False


def _table(data, prefixes):
    footer = data[-48:]
    if len(footer) < 48 or footer[-8:] != TABLE_MAGIC:
        raise LevelDBError('The Foundry database is damaged.')
    pos = 0
    _, pos = _varint(footer, pos)
    _, pos = _varint(footer, pos)
    index_offset, pos = _varint(footer, pos)
    index_size, pos = _varint(footer, pos)
    low = None
    for last_key, handle in _block_entries(_read_block(data, index_offset, index_size)):
        high = last_key[:-8]
        wanted = _overlaps(low, high, prefixes)
        low = high
        if not wanted:
            continue
        offset, pos = _varint(handle, 0)
        size, pos = _varint(handle, pos)
        for key, value in _block_entries(_read_block(data, offset, size)):
            tag = struct.unpack('<Q', key[-8:])[0]
            yield key[:-8], tag >> 8, tag & 0xFF, value


def _log(data):
    pos = 0
    pending = b''
    while pos + 7 <= len(data):
        left = LOG_BLOCK - pos % LOG_BLOCK
        if left < 7:
            pos += left
            continue
        size = int.from_bytes(data[pos + 4 : pos + 6], 'little')
        kind = data[pos + 6]
        pos += 7
        if kind == 0:
            continue
        chunk = data[pos : pos + size]
        pos += size
        if len(chunk) < size:
            return  # Foundry was mid-write; the unfinished record is not committed yet.
        pending = chunk if kind in (1, 2) else pending + chunk
        if kind not in (1, 4):
            continue
        batch, pending = pending, b''
        sequence = int.from_bytes(batch[:8], 'little')
        at = 12
        for index in range(int.from_bytes(batch[8:12], 'little')):
            action = batch[at]
            key_size, at = _varint(batch, at + 1)
            key = batch[at : at + key_size]
            at += key_size
            value = b''
            if action == PUT:
                value_size, at = _varint(batch, at)
                value = batch[at : at + value_size]
                at += value_size
            yield key, sequence + index, action, value


def _scan(folder, prefixes):
    best = {}
    for file in sorted(Path(folder).iterdir()):
        if file.is_symlink() or file.suffix not in ('.ldb', '.sst', '.log'):
            continue
        if file.stat().st_size > MAX_FILE_BYTES:
            raise LevelDBError('A Foundry database file is unexpectedly large.')
        data = file.read_bytes()
        rows = _log(data) if file.suffix == '.log' else _table(data, prefixes)
        for key, sequence, action, value in rows:
            if not key.startswith(prefixes):
                continue
            if key not in best or best[key][0] < sequence:
                best[key] = (sequence, action, value)
    return best


def read(folder, prefixes=(b'',)):
    """Return {key: value} for live keys starting with any prefix. Raises LevelDBError."""
    prefixes = tuple(prefixes)
    try:
        for attempt in range(3):
            try:
                best = _scan(folder, prefixes)
                break
            except FileNotFoundError:
                # A running Foundry compacts files away between our listing and our read.
                if attempt == 2:
                    raise
    except (IndexError, struct.error) as error:
        raise LevelDBError('The Foundry database is damaged.') from error
    return {key: value for key, (_, action, value) in best.items() if action == PUT}
