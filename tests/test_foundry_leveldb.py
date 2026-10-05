"""The standard-library LevelDB reader, checked against synthetic files."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'DM'))
sys.path.insert(0, str(ROOT / 'tests'))

import foundry_leveldb
import leveldb_writer as writer

PUT, DELETE = writer.PUT, writer.DELETE


class SnappyTests(unittest.TestCase):
    def test_literal_and_copy_elements(self):
        cases = {
            # literal "abc", then a 2-byte-offset copy that overlaps its own output
            b'abcabcabcabc': b'\x0c\x08abc' + bytes([(9 - 1) << 2 | 2, 3, 0]),
            # 1-byte-offset copy: length 4 + 2, offset 3
            b'xyzxyzxyz': b'\x09\x08xyz' + bytes([(3 >> 8) << 5 | (6 - 4) << 2 | 1, 3]),
            # 4-byte-offset copy
            b'0123401234': b'\x0a\x10' + b'01234' + bytes([(5 - 1) << 2 | 3, 5, 0, 0, 0]),
        }
        for expected, encoded in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(foundry_leveldb._snappy(encoded), expected)

    def test_long_literal(self):
        data = bytes(range(256)) * 2
        self.assertEqual(foundry_leveldb._snappy(writer.snappy_literal(data)), data)
        self.assertEqual(foundry_leveldb._snappy(writer.snappy_literal(b'x' * 61)), b'x' * 61)

    def test_bad_streams_are_rejected(self):
        for stream in (
            b'\x05\x04ab',  # copy offset beyond the output
            b'\x06\x08abc',  # declared length does not match
            b'\xff\xff\xff\xff\x7f\x00',  # declares a huge block
        ):
            with self.subTest(stream=stream), self.assertRaises(foundry_leveldb.LevelDBError):
                foundry_leveldb._snappy(stream)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-leveldb-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'journal'

    def rows(self, *items, sequence=1):
        return [(key, sequence, PUT, value) for key, value in sorted(items)]

    def test_tables_with_and_without_compression(self):
        items = [(b'!journal!%03d' % number, b'value %03d ' % number * 20) for number in range(40)]
        for compression in (0, 1):
            with self.subTest(compression=compression):
                folder = writer.database(
                    self.folder.with_name(f'c{compression}'),
                    tables=[writer.table(self.rows(*items), per_block=5, compression=compression)],
                )
                self.assertEqual(foundry_leveldb.read(folder), dict(items))

    def test_newest_value_and_deletions_win_across_files(self):
        old = writer.table(
            self.rows((b'!j!a', b'old a'), (b'!j!b', b'old b'), (b'!j!c', b'old c'), sequence=1)
        )
        newer = writer.table([(b'!j!a', 5, PUT, b'new a')])
        recent = writer.log([[(b'!j!b', None), (b'!j!d', b'fresh d')]], first_sequence=9)
        folder = writer.database(self.folder, tables=[old, newer], logs=[recent])
        self.assertEqual(
            foundry_leveldb.read(folder),
            {b'!j!a': b'new a', b'!j!c': b'old c', b'!j!d': b'fresh d'},
        )

    def test_a_deletion_older_than_a_put_does_not_hide_it(self):
        table = writer.table([(b'!j!a', 2, PUT, b'kept')])
        stale = writer.log([[(b'!j!a', None)]], first_sequence=1)
        folder = writer.database(self.folder, tables=[table], logs=[stale])
        self.assertEqual(foundry_leveldb.read(folder), {b'!j!a': b'kept'})

    def test_prefix_filter_skips_blocks_it_does_not_need(self):
        items = [(b'!journal!%d' % number, b'doc') for number in range(6)]
        items += [(b'!journal.pages!%d.p' % number, b'page') for number in range(6)]
        items += [(b'!journal.zzz!%d' % number, b'other') for number in range(6)]
        # Three rows per block; block 3 holds only `.pages` keys. Reading it would raise.
        rows = self.rows(*items)
        data = writer.table(rows, per_block=3, corrupt_block=3)
        folder = writer.database(self.folder, tables=[data])
        found = foundry_leveldb.read(folder, (b'!journal!',))
        self.assertEqual(sorted(found), [b'!journal!%d' % number for number in range(6)])
        with self.assertRaises(foundry_leveldb.LevelDBError):
            foundry_leveldb.read(folder, (b'!journal.pages!',))
        both = foundry_leveldb.read(
            writer.database(
                self.folder.with_name('both'), tables=[writer.table(rows, per_block=3)]
            ),
            (b'!journal!', b'!journal.pages!'),
        )
        self.assertEqual(len(both), 12)

    def test_log_records_that_span_blocks(self):
        big = b'x' * 70000
        folder = writer.database(
            self.folder, logs=[writer.log([[(b'!j!big', big)], [(b'!j!small', b'ok')]])]
        )
        self.assertEqual(foundry_leveldb.read(folder), {b'!j!big': big, b'!j!small': b'ok'})

    def test_unfinished_log_record_is_ignored(self):
        content = writer.log([[(b'!j!a', b'done')], [(b'!j!b', b'half written' * 10)]])
        folder = writer.database(self.folder, logs=[content[:-20]])
        self.assertEqual(foundry_leveldb.read(folder), {b'!j!a': b'done'})

    def test_damaged_files_raise_a_clear_error(self):
        good = writer.table(self.rows((b'!j!a', b'1')))
        for name, content in {
            'short': good[:30],
            'noise': b'\x07' * 200,
            'badcompression': writer.table(self.rows((b'!j!a', b'1')), corrupt_block=0),
        }.items():
            with self.subTest(name=name):
                folder = writer.database(self.folder.with_name(name), tables=[content])
                with self.assertRaises(foundry_leveldb.LevelDBError):
                    foundry_leveldb.read(folder)

    def test_a_file_compacted_away_mid_read_is_retried(self):
        folder = writer.database(self.folder, tables=[writer.table(self.rows((b'!j!a', b'1')))])
        real = foundry_leveldb._scan
        attempts = []

        def vanishing(*args):
            attempts.append(1)
            if len(attempts) < 3:
                raise FileNotFoundError('compacted')
            return real(*args)

        with patch.object(foundry_leveldb, '_scan', vanishing):
            self.assertEqual(foundry_leveldb.read(folder), {b'!j!a': b'1'})
        self.assertEqual(len(attempts), 3)
        with patch.object(foundry_leveldb, '_scan', side_effect=FileNotFoundError('gone')) as scan:
            with self.assertRaises(FileNotFoundError):
                foundry_leveldb.read(folder)
        self.assertEqual(scan.call_count, 3)

    def test_other_files_and_symlinks_are_ignored(self):
        folder = writer.database(self.folder, tables=[writer.table(self.rows((b'!j!a', b'1')))])
        (folder / 'notes.txt').write_text('ignore me')
        self.assertEqual(foundry_leveldb.read(folder), {b'!j!a': b'1'})


if __name__ == '__main__':
    unittest.main()
