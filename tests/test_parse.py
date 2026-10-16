import unittest
from email.message import EmailMessage
from mailbox_app.parse import parse_eml
from mailbox_app.paths import safe_component


class ParseTests(unittest.TestCase):
    def test_attachment_filename_is_utf8_byte_bounded_and_keeps_extension(self):
        msg = EmailMessage()
        msg.set_content('Body')
        for name in ('界' * 90 + '.txt', '\U00010400' * 90 + '.pdf'):
            msg.add_attachment(b'data', maintype='application', subtype='octet-stream', filename=name)
        parsed = parse_eml(msg.as_bytes(), 'abc', [])
        for (name, payload), extension in zip(parsed['attachments'], ('.txt', '.pdf')):
            self.assertLessEqual(len(name.encode('utf8')), 240)
            self.assertTrue(name.endswith(extension))
            self.assertEqual(safe_component(name), name)
            self.assertEqual(payload, b'data')

    def test_markdown_filename_with_unicode_subject_and_full_id_fits_filesystems(self):
        msg = EmailMessage()
        msg['Subject'] = '\U00010400' * 90
        msg.set_content('Body')
        gmail_id = 'a' * 128
        parsed = parse_eml(msg.as_bytes(), gmail_id, [])
        name = parsed['filename']
        self.assertLessEqual(len(name.encode('utf8')), 240)
        self.assertTrue(name.endswith('-' + gmail_id + '.md'))
        self.assertEqual(safe_component(name), name)

    def test_attached_forwarded_message_is_preserved_without_becoming_body(self):
        forwarded = EmailMessage()
        forwarded['Subject'] = 'Forwarded fixture'
        forwarded.set_content('Inner plain body must stay in the attachment.')
        forwarded.add_attachment(b'inner data', maintype='application', subtype='octet-stream', filename='inside.txt')
        msg = EmailMessage()
        msg.set_content('<p>Outer HTML body</p>', subtype='html')
        msg.add_attachment(forwarded, filename='forwarded.eml')
        parsed = parse_eml(msg.as_bytes(), 'abc', [])
        self.assertEqual(len(parsed['attachments']), 1)
        name, payload = parsed['attachments'][0]
        self.assertEqual(name, '001-forwarded.eml')
        self.assertEqual(payload, forwarded.as_bytes())
        self.assertEqual(parsed['frontmatter']['attachments'], [name])
        self.assertIn('Outer HTML body', parsed['body'])
        self.assertNotIn('Inner plain', parsed['body'])

    def test_truncation_cannot_leave_trailing_dot_or_space(self):
        msg = EmailMessage()
        msg.set_content('Body')
        for name in ('a' * 99 + '.txt', 'b' * 99 + ' name.txt'):
            msg.add_attachment(b'data', maintype='application', subtype='octet-stream', filename=name)
        parsed = parse_eml(msg.as_bytes(), 'abc', [])
        for name, payload in parsed['attachments']:
            self.assertFalse(name.endswith(('.', ' ')))
            self.assertEqual(safe_component(name), name)
            self.assertEqual(payload, b'data')

    def test_attachment_names_are_safe_unique_and_match_metadata(self):
        msg = EmailMessage()
        msg.set_content('Body')
        for name in ('../../evil.txt', 'CON', 'x:stream', 'same.txt', 'same.txt', 'SAME.txt'):
            msg.add_attachment(b'data', maintype='application', subtype='octet-stream', filename=name)
        parsed = parse_eml(msg.as_bytes(), 'abc', [])
        names = [name for name, _ in parsed['attachments']]
        self.assertEqual(names, parsed['frontmatter']['attachments'])
        self.assertEqual(len(names), len(set(n.casefold() for n in names)))
        for name in names:
            self.assertFalse(any(c in name for c in '/\\:'))
            self.assertNotIn(name.upper(), ('CON', 'NUL'))
            self.assertFalse(name.startswith('.'))

    def test_html_conversion_discards_active_and_head_content(self):
        msg = EmailMessage()
        msg.set_content('<head><title>hidden title</title></head><h1>Hello</h1><script>hidden script</script><style>hidden style</style><p>A <a href="https://example.com">link</a></p>', subtype='html')
        body = parse_eml(msg.as_bytes(), 'abc', [])['body']
        self.assertIn('# Hello', body)
        self.assertIn('[link](https://example.com)', body)
        self.assertNotIn('hidden', body)

    def test_plain_text_is_preferred_and_not_reformatted(self):
        msg = EmailMessage()
        msg.set_content('  plain\n\n  spaced  \n')
        msg.add_alternative('<p>HTML</p>', subtype='html')
        self.assertEqual(parse_eml(msg.as_bytes(), 'abc', [])['body'], '  plain\n\n  spaced  \n')
