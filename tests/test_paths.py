import tempfile
import unittest
import subprocess
import sys
import json
import time
import os
from unittest.mock import patch
from pathlib import Path

from mailbox_app.paths import Paths


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = Paths({'data_dir': self.temp.name, 'oauth_client_secrets': 'unused'})
        self.paths.ensure()

    def test_user_paths_reject_windows_and_internal_escapes(self):
        for name in ('../escape', '/tmp/x', r'C:\temp\x', r'\\server\share',
                     'inbox/x:stream', 'inbox/CON.md', 'inbox/NUL', '.auth/token.json',
                     'attachments/id/file', 'inbox/../sent/x', 'inbox/x.', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.paths.user_path(name)
        self.assertEqual(self.paths.user_path('', allow_root=True), self.paths.data_dir)
        self.assertEqual(self.paths.user_path('inbox/note.md'), self.paths.inbox_dir / 'note.md')

    def test_ids_are_single_safe_components(self):
        for name in ('../x', 'a/b', r'a\b', 'C:x', '', 'CON', '.state'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.paths.original_path(name)

    def test_id_search_is_exact_frontmatter_only(self):
        (self.paths.inbox_dir / 'wrong.md').write_text('---\nid: abcdef\n---\nid: abc\n', encoding='utf8')
        self.assertIsNone(self.paths.find_md_by_id('abc'))
        right = self.paths.inbox_dir / 'right.md'
        right.write_text('---\nid: abc\n---\nbody', encoding='utf8')
        self.assertEqual(self.paths.find_md_by_id('abc'), right)

    def test_original_alone_does_not_mean_complete(self):
        self.paths.original_path('abc').write_bytes(b'partial')
        self.assertFalse(self.paths.has_message('abc'))

    def test_failed_atomic_replace_preserves_previous_file(self):
        target = self.paths.original_path('abc')
        target.write_bytes(b'previous')
        with patch('mailbox_app.paths.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.paths.atomic_write(target, b'new')
        self.assertEqual(target.read_bytes(), b'previous')
        self.assertEqual([p.name for p in target.parent.iterdir()], ['abc.eml'])

    @unittest.skipIf(os.name == 'nt', 'Directory fsync is POSIX-specific')
    def test_atomic_replace_flushes_file_and_directory(self):
        with patch('mailbox_app.paths.os.fsync', wraps=os.fsync) as fsync:
            self.paths.atomic_write(self.paths.original_path('abc'), b'new')
        self.assertEqual(fsync.call_count, 2)

    def test_malformed_user_note_does_not_prevent_import_search(self):
        (self.paths.inbox_dir / 'broken.md').write_text('---\nid: [broken\n---\n', encoding='utf8')
        self.assertIsNone(self.paths.find_md_by_id('abc'))

    def test_symlink_paths_are_rejected(self):
        link = self.paths.inbox_dir / 'link'
        try:
            link.symlink_to(self.paths.sent_dir, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            self.paths.user_path('inbox/link/file.md')

    def test_mutation_lock_is_reentrant_across_paths_instances(self):
        other = Paths(self.paths.config)
        with self.paths.mutation_lock(), other.mutation_lock():
            self.assertTrue(self.paths.state_dir.is_dir())

    def test_mutation_lock_blocks_a_separate_process(self):
        ready = self.paths.data_dir / 'ready'
        entered = self.paths.data_dir / 'entered'
        code = '''import json,sys
from mailbox_app.paths import Paths
paths = Paths(json.loads(sys.argv[1]))
(paths.data_dir / 'ready').write_text('ready')
with paths.mutation_lock():
    (paths.data_dir / 'entered').write_text('entered')
'''
        process = None
        try:
            with self.paths.mutation_lock():
                process = subprocess.Popen([sys.executable, '-c', code, json.dumps(self.paths.config)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                time.sleep(.15)
                self.assertFalse(entered.exists())
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertTrue(entered.exists())
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
