import base64
import tempfile
import unittest
import json
import subprocess
import sys
from pathlib import Path
from email.message import EmailMessage
from email import message_from_bytes, policy
import frontmatter
from unittest.mock import patch
from googleapiclient.errors import HttpError
from httplib2 import Response

from mailbox_app.paths import Paths
from mailbox_app import sync, send


class FakeService:
    def __init__(self, raw):
        self.raw = raw
        self.sends = 0
        self.mode = 'normal'
        self.canonical_raw = None
        self.fetch_error = False
        self.fetches = 0
    def users(self): return self
    def messages(self): return self
    def getProfile(self, **kwargs): return Result({'emailAddress': 'test@example.com'})
    def list(self, **kwargs): return Result({'messages': [{'id': 'abc'}]})
    def get(self, **kwargs):
        if kwargs['id'] == 'sentabc':
            self.fetches += 1
            if self.fetch_error:
                return Result(error=TimeoutError('private fetch details'))
            return Result({'id': 'sentabc', 'raw': base64.urlsafe_b64encode(self.canonical_raw).decode(), 'labelIds': ['SENT']})
        return Result({'raw': base64.urlsafe_b64encode(self.raw).decode(), 'labelIds': ['INBOX']})
    def send(self, **kwargs):
        self.sends += 1
        if self.mode == 'unknown':
            return Result(error=TimeoutError('private detail'))
        canonical = message_from_bytes(base64.urlsafe_b64decode(kwargs['body']['raw']), policy=policy.default)
        canonical.replace_header('Message-ID', '<canonical-sentabc@mail.gmail.com>')
        self.canonical_raw = canonical.as_bytes()
        return Result({'id': 'sentabc', 'labelIds': ['SENT']})


class Result:
    def __init__(self, result=None, error=None): self.result, self.error = result, error
    def execute(self):
        if self.error: raise self.error
        return self.result


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = Paths({'data_dir': self.temp.name, 'oauth_client_secrets': 'unused'})
        self.paths.ensure()
        msg = EmailMessage()
        msg['Subject'] = 'fixture'
        msg.set_content('mail body')
        msg.add_attachment(b'attachment', maintype='application', subtype='octet-stream', filename='../test.txt')
        self.service = FakeService(msg.as_bytes())

    def test_sent_mirror_uses_canonical_gmail_message_id_and_raw(self):
        with patch.object(send, 'build_service', return_value=self.service):
            result = send.send_message(self.paths, to='to@example.com', body='test', operation_id='canonical')
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(self.paths.original_path('sentabc').read_bytes(), self.service.canonical_raw)
        note = frontmatter.load(self.paths.find_md_by_id('sentabc'))
        self.assertEqual(note['message-id'], '<canonical-sentabc@mail.gmail.com>')
        self.assertEqual(self.service.sends, 1)
        self.assertEqual(self.service.fetches, 1)

    def test_failed_canonical_fetch_is_accepted_and_retry_never_resends(self):
        self.service.fetch_error = True
        with patch.object(send, 'build_service', return_value=self.service):
            result = send.send_message(self.paths, to='to@example.com', operation_id='fetch-failed')
            self.assertEqual(result['status'], 'sent_unmirrored')
            self.assertEqual(result['id'], 'sentabc')
            self.assertFalse(self.paths.original_path('sentabc').exists())
            self.assertIsNone(self.paths.find_md_by_id('sentabc'))
            self.service.fetch_error = False
            retry = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='fetch-failed')
            self.assertEqual(retry['status'], 'sent')
        self.assertEqual(self.service.sends, 1)
        self.assertEqual(self.service.fetches, 2)
        self.assertEqual(self.paths.original_path('sentabc').read_bytes(), self.service.canonical_raw)

    def test_cached_canonical_message_can_finish_mirroring_without_network(self):
        with patch.object(send, 'build_service', return_value=self.service), patch.object(sync, '_write_md', side_effect=OSError('disk')):
            first = send.send_message(self.paths, to='to@example.com', operation_id='cached-canonical')
        self.assertEqual(first['status'], 'sent_unmirrored')
        with patch.object(send, 'build_service', side_effect=AssertionError('Must reuse cached canonical message')):
            second = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='cached-canonical')
        self.assertEqual(second['status'], 'sent')
        self.assertEqual(self.paths.original_path('sentabc').read_bytes(), self.service.canonical_raw)
        self.assertEqual(self.service.sends, 1)
        self.assertEqual(self.service.fetches, 1)

    def test_canonical_cache_disk_failure_keeps_acceptance_and_never_resends(self):
        original_save = send._save
        def fail_cache(paths, journal, state):
            if 'canonical_raw' in state:
                raise OSError('disk failure')
            return original_save(paths, journal, state)
        with patch.object(send, 'build_service', return_value=self.service):
            with patch.object(send, '_save', side_effect=fail_cache):
                first = send.send_message(self.paths, to='to@example.com', operation_id='cache-failure')
            self.assertEqual(first['status'], 'sent_unmirrored')
            self.assertFalse(self.paths.original_path('sentabc').exists())
            state = json.loads((self.paths.state_dir / 'sends' / 'cache-failure.json').read_text(encoding='utf8'))
            self.assertEqual(state['status'], 'accepted')
            retry = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='cache-failure')
        self.assertEqual(retry['status'], 'sent')
        self.assertEqual(self.service.sends, 1)
        self.assertEqual(self.service.fetches, 2)

    def test_incomplete_or_mismatched_canonical_response_is_not_mirrored(self):
        cases = [
            {'id': 'sentabc', 'raw': ''},
            {'id': 'wrongid', 'raw': base64.urlsafe_b64encode(b'Message-ID: <other@example.test>\n\nbody').decode()},
            {'id': 'sentabc', 'raw': base64.urlsafe_b64encode(b'Subject: missing ID\n\nbody').decode()},
        ]
        with patch.object(send, 'build_service', return_value=self.service):
            for index, response in enumerate(cases):
                with self.subTest(response=response), patch.object(self.service, 'get', return_value=Result(response)):
                    result = send.send_message(self.paths, to='to@example.com', operation_id=f'bad-canonical-{index}')
                    self.assertEqual(result['status'], 'sent_unmirrored')
                    self.assertFalse(self.paths.original_path('sentabc').exists())
                    self.assertIsNone(self.paths.find_md_by_id('sentabc'))

    def test_legacy_completed_journal_preserves_moved_edited_note_and_original(self):
        with patch.object(send, 'build_service', return_value=self.service):
            send.send_message(self.paths, to='to@example.com', operation_id='legacy-complete')
        journal = self.paths.state_dir / 'sends' / 'legacy-complete.json'
        state = json.loads(journal.read_text(encoding='utf8'))
        state.pop('canonical_raw', None)
        state.pop('canonical_required', None)
        journal.write_text(json.dumps(state), encoding='utf8')
        note = self.paths.find_md_by_id('sentabc')
        moved = self.paths.data_dir / 'archive' / 'mine.md'
        moved.parent.mkdir()
        note.replace(moved)
        moved.write_text(moved.read_text(encoding='utf8') + '\nmy edit', encoding='utf8')
        expected = moved.read_bytes()
        self.paths.original_path('sentabc').write_bytes(b'legacy original unchanged')
        with patch.object(send, 'build_service', side_effect=AssertionError('No automatic legacy repair')):
            result = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='legacy-complete')
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['path'], 'archive/mine.md')
        self.assertEqual(moved.read_bytes(), expected)
        self.assertEqual(self.paths.original_path('sentabc').read_bytes(), b'legacy original unchanged')

    def test_legacy_accepted_journal_with_existing_note_never_repairs_or_resends(self):
        with patch.object(send, 'build_service', return_value=self.service):
            send.send_message(self.paths, to='to@example.com', operation_id='legacy-accepted')
        journal = self.paths.state_dir / 'sends' / 'legacy-accepted.json'
        state = json.loads(journal.read_text(encoding='utf8'))
        state.pop('canonical_raw')
        state.pop('canonical_required')
        state['status'] = 'accepted'
        journal.write_text(json.dumps(state), encoding='utf8')
        original = self.paths.original_path('sentabc')
        original.write_bytes(b'legacy submitted MIME')
        note = self.paths.find_md_by_id('sentabc')
        note_bytes = note.read_bytes()
        with patch.object(send, 'build_service', side_effect=AssertionError('No automatic legacy repair')):
            result = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='legacy-accepted')
        self.assertEqual(result['status'], 'sent_unmirrored')
        self.assertEqual(result['id'], 'sentabc')
        self.assertEqual(note.read_bytes(), note_bytes)
        self.assertEqual(original.read_bytes(), b'legacy submitted MIME')
        self.assertEqual(self.service.sends, 1)

    def test_sync_recovers_from_parse_failure_after_original_saved(self):
        with patch.object(sync, 'build_service', return_value=self.service):
            with patch.object(sync, 'parse_eml', side_effect=ValueError('private path secret')):
                failed = sync.sync(self.paths, days=1)
            self.assertEqual(len(failed['failures']), 1)
            self.assertNotIn('secret', str(failed))
            self.assertTrue(self.paths.original_path('abc').exists())
            self.assertFalse(self.paths.has_message('abc'))
            recovered = sync.sync(self.paths, days=1)
            self.assertEqual(recovered['new'], 1)
            self.assertTrue(self.paths.has_message('abc'))

    def test_incomplete_retry_preserves_moved_edited_note_and_rebuilds_attachment(self):
        with patch.object(sync, 'build_service', return_value=self.service):
            sync.sync(self.paths, days=1)
            original = self.paths.find_md_by_id('abc')
            moved = self.paths.data_dir / 'archive' / 'edited.md'
            moved.parent.mkdir()
            original.replace(moved)
            moved.write_text(moved.read_text(encoding='utf8') + '\nuser edit', encoding='utf8')
            expected = moved.read_bytes()
            next(self.paths.attachment_dir('abc').iterdir()).unlink()
            result = sync.sync(self.paths, days=1)
            self.assertEqual(result['new'], 1)
            self.assertEqual(moved.read_bytes(), expected)
            self.assertEqual(len(list(self.paths.data_dir.rglob('*.md'))), 1)
            self.assertTrue(self.paths.has_message('abc'))

    def test_accepted_send_retries_only_local_mirror_even_after_restart(self):
        with patch.object(send, 'build_service', return_value=self.service):
            with patch.object(sync, '_write_md', side_effect=OSError('disk secret')):
                first = send.send_message(self.paths, to='to@example.com', body='test', operation_id='op-1')
            self.assertEqual(first['status'], 'sent_unmirrored')
            restarted = Paths(self.paths.config)
            second = send.send_message(restarted, to='to@example.com', body='test', operation_id='op-1')
            self.assertEqual(second['status'], 'sent')
            self.assertEqual(self.service.sends, 1)
            with self.assertRaises(ValueError):
                send.send_message(restarted, to='other@example.com', body='test', operation_id='op-1')
            self.assertEqual(self.service.sends, 1)

    def test_unknown_send_is_never_resent(self):
        self.service.mode = 'unknown'
        with patch.object(send, 'build_service', return_value=self.service):
            for paths in (self.paths, Paths(self.paths.config)):
                result = send.send_message(paths, to='to@example.com', operation_id='op-unknown')
                self.assertEqual(result['status'], 'unknown')
                self.assertNotIn('private', str(result))
        self.assertEqual(self.service.sends, 1)

    def test_sending_does_not_resolve_or_expose_the_local_hostname(self):
        with patch.object(send, 'build_service', return_value=self.service), patch(
            'socket.getfqdn', side_effect=AssertionError('Local hostname lookup is unavailable')
        ):
            result = send.send_message(self.paths, to='to@example.com', operation_id='no-dns')
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(self.service.sends, 1)

    def test_unknown_send_survives_fresh_python_process(self):
        self.service.mode = 'unknown'
        with patch.object(send, 'build_service', return_value=self.service):
            send.send_message(self.paths, to='to@example.com', operation_id='op-process')
        program = '''import json,sys
from mailbox_app.paths import Paths
from mailbox_app import send
def forbidden(*args): raise AssertionError('Unexpected Gmail access')
send.build_service = forbidden
print(json.dumps(send.send_message(Paths(json.loads(sys.argv[1])), to='to@example.com', operation_id='op-process')))
'''
        result = subprocess.run([sys.executable, '-c', program, json.dumps(self.paths.config)], capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'unknown')
        self.assertEqual(self.service.sends, 1)

    def test_disk_failure_before_intent_never_sends(self):
        with patch.object(send, 'build_service', return_value=self.service), patch.object(self.paths, 'atomic_write', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                send.send_message(self.paths, to='to@example.com', operation_id='op-before')
        self.assertEqual(self.service.sends, 0)

    def test_failed_acceptance_journal_is_not_retried(self):
        original_save = send._save
        def fail_acceptance(paths, journal, state):
            if state['status'] == 'accepted': raise OSError('disk')
            return original_save(paths, journal, state)
        with patch.object(send, 'build_service', return_value=self.service), patch.object(send, '_save', side_effect=fail_acceptance):
            first = send.send_message(self.paths, to='to@example.com', operation_id='op-accept')
            self.assertEqual(first['status'], 'sent_unmirrored')
        with patch.object(send, 'build_service', side_effect=AssertionError('Must not reconnect')):
            second = send.send_message(Paths(self.paths.config), to='to@example.com', operation_id='op-accept')
            self.assertEqual(second['status'], 'unknown')
        self.assertEqual(self.service.sends, 1)

    def test_known_rejection_is_sanitized_and_not_automatically_retried(self):
        failure = HttpError(Response({'status': '400'}), b'private detail')
        with patch.object(send, 'build_service', return_value=self.service), patch.object(self.service, 'send', return_value=Result(error=failure)) as call:
            for _ in range(2):
                result = send.send_message(self.paths, to='to@example.com', operation_id='op-rejected')
                self.assertEqual(result['status'], 'rejected')
                self.assertNotIn('private', str(result))
            self.assertEqual(call.call_count, 1)

    def test_duplicate_import_and_same_prefix_ids_do_not_overwrite(self):
        sync.import_message(self.paths, self.service.raw, 'abcdefgh1', ['INBOX'])
        sync.import_message(self.paths, self.service.raw, 'abcdefgh2', ['INBOX'])
        sync.import_message(self.paths, self.service.raw, 'abcdefgh1', ['INBOX'])
        self.assertEqual(len(list(self.paths.inbox_dir.glob('*.md'))), 2)
        self.assertTrue(self.paths.has_message('abcdefgh1'))
        self.assertTrue(self.paths.has_message('abcdefgh2'))
