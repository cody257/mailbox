import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mailbox_app import server
from mailbox_app.paths import Paths


class ServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = Paths({'data_dir': self.tmp.name, 'oauth_client_secrets': 'unused', 'server_port': 5321})
        self.paths.ensure()
        (self.paths.inbox_dir / 'note.md').write_text('---\nsubject: Note\n---\nHello', encoding='utf-8')
        (self.paths.auth_dir / 'secret.md').write_text('PRIVATE', encoding='utf-8')
        self.app = server.create_app(self.paths)
        self.app.testing = True
        self.client = self.app.test_client()
        self.origin = 'http://localhost:5321'
        self.headers = {'X-Mailbox-Token': self.app.config['MAILBOX_TOKEN'], 'Origin': self.origin}

    def get(self, path, **kwargs):
        return self.client.get(path, base_url=self.origin, headers=self.headers, **kwargs)

    def post(self, path, data):
        return self.client.post(path, json=data, base_url=self.origin, headers=self.headers)

    def test_index_assets_and_api_security(self):
        response = self.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.app.config['MAILBOX_TOKEN'].encode(), response.data)
        self.assertNotIn(b'cdn.jsdelivr', response.data)
        with self.get('/static/app.js') as asset:
            self.assertEqual(asset.status_code, 200)
        self.assertEqual(self.client.get('/api/folders', base_url=self.origin).status_code, 403)
        self.assertEqual(self.get('/api/folders').status_code, 200)
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', self.get('/api/folders').headers['Cache-Control'])

    def test_host_remote_origin_and_json_rejected(self):
        for host in ['http://evil.test:5321', 'http://localhost:5322']:
            self.assertEqual(self.client.get('/', base_url=host).status_code, 403)
        self.assertEqual(self.get('/', environ_overrides={'REMOTE_ADDR': '192.0.2.5'}).status_code, 403)
        self.assertEqual(self.get('/', environ_overrides={'REMOTE_ADDR': ''}).status_code, 403)
        for origin in ['https://evil.test', 'null', 'http://127.0.0.1:5321', '']:
            self.assertEqual(self.client.post('/api/folders', base_url=self.origin, json={'name': 'New'}, headers={**self.headers, 'Origin': origin}).status_code, 403)
        self.assertEqual(self.client.post('/api/folders', base_url=self.origin, data='{}', headers=self.headers).status_code, 415)

    def test_proxy_headers_cannot_authorize_remote_client(self):
        response = self.client.get('/', base_url='http://evil.test', headers={'X-Forwarded-Host': 'localhost:5321', 'X-Forwarded-For': '127.0.0.1'}, environ_overrides={'REMOTE_ADDR': '192.0.2.5'})
        self.assertEqual(response.status_code, 403)

    def test_tokens_are_per_app_and_all_mutations_are_gated(self):
        other = server.create_app(self.paths)
        self.assertNotEqual(other.config['MAILBOX_TOKEN'], self.app.config['MAILBOX_TOKEN'])
        for endpoint in ['send', 'sync', 'move', 'folders', 'open-file', 'open-folder']:
            response = self.client.post('/api/' + endpoint, base_url=self.origin, json={}, headers={'Origin': self.origin})
            self.assertEqual(response.status_code, 403, endpoint)
        response = self.client.get('/api/folders', base_url=self.origin, headers={'X-Mailbox-Token': '\u00e9'})
        self.assertEqual(response.status_code, 403)

    def test_malformed_payloads_are_sanitized(self):
        for data in [None, [], 'text', 10]:
            response = self.client.post('/api/folders', base_url=self.origin, json=data, headers={**self.headers, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 400)
        for data in [{'name': []}, {'parent': {}}, {'name': 'CON'}, {'name': 'Trailing.'}]:
            self.assertEqual(self.post('/api/folders', data).status_code, 400)

    def test_private_paths_and_native_open_restrictions(self):
        with patch.object(server, '_open_native') as native:
            for path in ['.auth/secret.md', '../secret.md', 'attachments/abc/file.md', '/inbox/note.md', 'inbox/../.auth/secret.md']:
                self.assertEqual(self.get('/api/message', query_string={'path': path}).status_code, 400, path)
                self.assertEqual(self.post('/api/open-file', {'path': path}).status_code, 400, path)
            self.assertEqual(self.post('/api/open-file', {'path': 'inbox/note.md'}).status_code, 200)
            native.assert_called_once()
            native.reset_mock()
            self.assertEqual(self.post('/api/open-folder', {'path': ''}).status_code, 400)
            self.assertEqual(self.post('/api/open-file', {'path': 'inbox/run.exe'}).status_code, 400)
            native.assert_not_called()

    def test_move_and_folder_creation(self):
        self.assertEqual(self.post('/api/folders', {'name': 'Archive'}).status_code, 200)
        self.assertEqual(self.post('/api/move', {'path': 'inbox/note.md', 'target_folder': 'Archive'}).status_code, 200)
        self.assertTrue((self.paths.data_dir / 'Archive/note.md').is_file())
        self.assertEqual(self.post('/api/folders', {'name': 'attachments'}).status_code, 400)

    def test_malformed_note_does_not_hide_other_messages(self):
        (self.paths.inbox_dir / 'broken.md').write_text('---\nsubject: [unclosed\n---\nbroken', encoding='utf-8')
        self.assertEqual(self.get('/api/folders').status_code, 200)
        response = self.get('/api/messages?folder=inbox')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([message['subject'] for message in response.json], ['Note'])

    def test_send_operation_id_and_sanitized_failure(self):
        with patch('mailbox_app.send.send_message', return_value={'status': 'sent_unmirrored', 'id': 'abc', 'path': None}) as send:
            response = self.post('/api/send', {'operation_id': '01234567-89ab-cdef-0123-456789abcdef', 'to': 'a@example.test', 'body': 'Hello'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['status'], 'sent_unmirrored')
            self.assertEqual(send.call_args.kwargs['operation_id'], '01234567-89ab-cdef-0123-456789abcdef')
        with patch('mailbox_app.send.send_message', side_effect=RuntimeError('PRIVATE_CREDENTIAL_PATH')):
            response = self.post('/api/send', {'operation_id': '01234567-89ab-cdef-0123-456789abcdef'})
            self.assertEqual(response.status_code, 500)
            self.assertNotIn(b'PRIVATE_CREDENTIAL_PATH', response.data)

    def test_authorization_failure_has_actionable_safe_response(self):
        from mailbox_app.sync import GmailAuthorizationError
        with patch('mailbox_app.send.send_message', side_effect=GmailAuthorizationError('Authorize Gmail readonly and send permissions.')):
            response = self.post('/api/send', {'operation_id': 'auth-test', 'to': 'a@example.test'})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json['code'], 'authorization_required')
            self.assertEqual(response.json['error'], 'Authorize Gmail readonly and send permissions.')

    def test_attachment_auth_and_containment(self):
        attachment_dir = self.paths.attachment_dir('abc123')
        attachment_dir.mkdir()
        (attachment_dir / 'file.txt').write_text('Attachment', encoding='utf-8')
        with self.get('/api/attachment/abc123/file.txt') as attachment:
            self.assertEqual(attachment.status_code, 200)
            self.assertEqual(attachment.mimetype, 'application/octet-stream')
        self.assertEqual(self.get('/api/attachment/abc123/../file.txt').status_code, 400)
        self.assertEqual(self.client.get('/api/attachment/abc123/file.txt', base_url=self.origin).status_code, 403)


if __name__ == '__main__':
    unittest.main()
