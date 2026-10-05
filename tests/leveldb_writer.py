"""Write small synthetic LevelDB folders so reader tests need no real Foundry data."""

import struct
from pathlib import Path

TABLE_MAGIC = bytes.fromhex('57fb808b247547db')
PUT, DELETE = 1, 0


def varint(value):
    out = bytearray()
    while value >= 0x80:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def snappy_literal(data):
    """A valid Snappy stream made of one literal element."""
    size = len(data) - 1
    if size < 60:
        header = bytes([size << 2])
    elif size < 256:
        header = bytes([60 << 2, size])
    else:
        header = bytes([61 << 2]) + struct.pack('<H', size)
    return varint(len(data)) + header + data


def _block(entries, restart_every=4):
    out = bytearray()
    restarts = []
    previous = b''
    for index, (key, value) in enumerate(entries):
        shared = 0
        if index % restart_every:
            while shared < min(len(previous), len(key)) and previous[shared] == key[shared]:
                shared += 1
        else:
            restarts.append(len(out))
        out += varint(shared) + varint(len(key) - shared) + varint(len(value))
        out += key[shared:] + value
        previous = key
    for restart in restarts or [0]:
        out += struct.pack('<I', restart)
    out += struct.pack('<I', len(restarts) or 1)
    return bytes(out)


def _stored(block, compression):
    body = snappy_literal(block) if compression == 1 else block
    return body + bytes([compression]) + b'\0\0\0\0'


def table(rows, per_block=3, compression=0, corrupt_block=None):
    """rows: (user_key, sequence, kind, value) sorted by key. Returns .ldb bytes.

    `corrupt_block` marks one data block's compression byte invalid, to prove a reader skipped it.
    """
    data = bytearray()
    index = []
    chunks = [rows[start : start + per_block] for start in range(0, len(rows), per_block)]
    for number, chunk in enumerate(chunks):
        entries = [
            (key + struct.pack('<Q', seq << 8 | kind), value) for key, seq, kind, value in chunk
        ]
        stored = _stored(_block(entries), compression)
        if number == corrupt_block:
            stored = stored[:-5] + bytes([9]) + stored[-4:]
        offset = len(data)
        data += stored
        last = entries[-1][0]
        index.append((last, varint(offset) + varint(len(stored) - 5)))
    index_stored = _stored(_block(index), 0)
    index_offset = len(data)
    data += index_stored
    meta_stored = _stored(_block([]), 0)
    meta_offset = len(data)
    data += meta_stored
    footer = varint(meta_offset) + varint(len(meta_stored) - 5)
    footer += varint(index_offset) + varint(len(index_stored) - 5)
    footer = footer.ljust(40, b'\0') + TABLE_MAGIC
    return bytes(data + footer)


def log(batches, first_sequence=1):
    """batches: lists of (key, value or None). Returns .log bytes using 32 KiB blocks."""
    out = bytearray()
    sequence = first_sequence
    for batch in batches:
        body = bytearray(struct.pack('<QI', sequence, len(batch)))
        for key, value in batch:
            if value is None:
                body += bytes([DELETE]) + varint(len(key)) + key
            else:
                body += bytes([PUT]) + varint(len(key)) + key + varint(len(value)) + value
        sequence += len(batch)
        rest = bytes(body)
        first = True
        while True:
            room = 32768 - len(out) % 32768
            if room < 7:
                out += b'\0' * room
                room = 32768
            piece, rest = rest[: room - 7], rest[room - 7 :]
            last = not rest
            kind = 1 if first and last else 2 if first else 4 if last else 3
            out += b'\0\0\0\0' + struct.pack('<HB', len(piece), kind) + piece
            first = False
            if last:
                break
    return bytes(out)


def database(folder, tables=(), logs=()):
    """Create a folder with numbered table and log files (and the usual bookkeeping files)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    for content in tables:
        (folder / f'{number:06d}.ldb').write_bytes(content)
        number += 1
    for content in logs:
        (folder / f'{number:06d}.log').write_bytes(content)
        number += 1
    (folder / 'CURRENT').write_text('MANIFEST-000001\n')
    (folder / 'LOCK').write_bytes(b'')
    (folder / 'LOG').write_text('not a database file')
    return folder
