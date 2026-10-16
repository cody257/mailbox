import tempfile
import unittest
from unittest.mock import patch, MagicMock
from mailbox_app.paths import Paths
from mailbox_app import sync


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = Paths({'data_dir': self.temp.name, 'oauth_client_secrets': self.temp.name + '/secret-client.json'})
        self.paths.ensure()

    def test_only_readonly_and_send_scopes(self):
        self.assertEqual(set(sync.SCOPES), {'https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send'})

    def test_missing_config_does_not_expose_private_paths(self):
        with self.assertRaises(Exception) as caught:
            sync.get_credentials(self.paths)
        self.assertNotIn(self.temp.name, str(caught.exception))
        self.assertIn('Desktop OAuth', str(caught.exception))

    def test_failed_refresh_gives_actionable_error_without_launching_browser(self):
        self.paths.token_path.write_text('{}')
        creds = MagicMock(valid=False, expired=True, refresh_token='fake', scopes=sync.SCOPES, granted_scopes=None)
        creds.refresh.side_effect = RuntimeError('secret token')
        with patch.object(sync.Credentials, 'from_authorized_user_file', return_value=creds), patch.object(sync.InstalledAppFlow, 'from_client_secrets_file') as launch:
            with self.assertRaises(Exception) as caught:
                sync.get_credentials(self.paths)
            self.assertNotIn('secret token', str(caught.exception))
            self.assertIn('authorize', str(caught.exception).lower())
            launch.assert_not_called()

    def test_valid_saved_credentials_with_wrong_scopes_require_reauthorization(self):
        self.paths.token_path.write_text('{}')
        for scopes in ([sync.SCOPES[0]], ['https://www.googleapis.com/auth/gmail.modify'], None):
            creds = MagicMock(valid=True, scopes=scopes, granted_scopes=None)
            with self.subTest(scopes=scopes), patch.object(sync.Credentials, 'from_authorized_user_file', return_value=creds) as load, patch.object(sync.InstalledAppFlow, 'from_client_secrets_file') as launch:
                with self.assertRaises(RuntimeError) as caught:
                    sync.get_credentials(self.paths)
                self.assertIn('authorize', str(caught.exception).lower())
                self.assertEqual(len(load.call_args.args), 1)
                launch.assert_not_called()

    def test_valid_exact_scopes_are_reused_without_consent(self):
        self.paths.token_path.write_text('{}')
        creds = MagicMock(valid=True, scopes=sync.SCOPES, granted_scopes=None)
        with patch.object(sync.Credentials, 'from_authorized_user_file', return_value=creds), patch.object(sync.InstalledAppFlow, 'from_client_secrets_file') as launch:
            self.assertIs(sync.get_credentials(self.paths), creds)
            launch.assert_not_called()
