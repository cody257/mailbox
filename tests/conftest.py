"""Real HTTP and disk fixtures. Only Gmail transport/native launch are replaced."""
import base64
import json
import threading
from email.message import EmailMessage
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from werkzeug.serving import make_server


class Call:
    def __init__(self, action):
        self.action = action

    def execute(self):
        return self.action()


def raw_message(subject, message_id, body, attachment=None):
    msg = EmailMessage()
    msg["From"] = "Alice Example <alice@example.test>"
    msg["To"] = "owner@example.test"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 15 Oct 2026 12:00:00 +0000"
    msg["Message-ID"] = f"<{message_id}@example.test>"
    msg["References"] = "<ancestor@example.test>"
    msg.set_content(body)
    if attachment:
        msg.add_attachment(attachment, maintype="application", subtype="octet-stream", filename="evidence.bin")
    return msg.as_bytes()


class GmailCapture:
    """A paginated Gmail wire boundary, with durable send evidence."""
    def __init__(self, root, paths):
        self.root, self.paths = root, paths
        self.attachment = bytes(range(256)) + b"\x00synthetic attachment\xff"
        self.messages_by_id = {
            "inbox001": raw_message("Quarterly evidence", "inbox001", "Independent fixture body.\n\n**Bold evidence**", self.attachment),
            "inbox002": raw_message("Second page message", "inbox002", "Pagination must include this message."),
        }
        self.mode = "success"
        self.pages = []
        self.fetches = []
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        def action():
            token = kwargs.get("pageToken")
            self.pages.append(token)
            ids = list(self.messages_by_id)
            if token is None:
                return {"messages": [{"id": ids[0]}], "nextPageToken": "page-two"}
            assert token == "page-two"
            return {"messages": [{"id": item} for item in ids[1:]]}
        return Call(action)

    def get(self, **kwargs):
        def action():
            identifier = kwargs["id"]
            self.fetches.append(identifier)
            if self.mode == 'canonical_failure' and identifier.startswith('sent'):
                raise TimeoutError('Synthetic canonical-message fetch failed')
            labels = ['SENT'] if identifier.startswith('sent') else ['INBOX', 'UNREAD']
            return {"id": identifier, "raw": base64.urlsafe_b64encode(self.messages_by_id[identifier]).decode(), "labelIds": labels, "internalDate": "1792065600000"}
        return Call(action)

    def getProfile(self, **kwargs):
        return Call(lambda: {"emailAddress": "owner@example.test"})

    def send(self, **kwargs):
        def action():
            raw = base64.urlsafe_b64decode(kwargs["body"]["raw"])
            index = len(self.sent) + 1
            self.sent.append(raw)
            (self.root / f"captured-{index}.eml").write_bytes(raw)
            (self.root / "capture.json").write_text(json.dumps({"calls": len(self.sent), "mode": self.mode}))
            if self.mode == "unknown":
                raise TimeoutError("Synthetic transport lost response after acceptance")
            if self.mode == "rejected":
                from googleapiclient.errors import HttpError
                from httplib2 import Response
                raise HttpError(Response({"status": "400"}), b'{"error":{"message":"Synthetic rejection"}}')
            identifier = f"sent{index:04d}"
            canonical = message_from_bytes(raw, policy=policy.default)
            canonical.replace_header('Message-ID', f'<canonical-{identifier}@mail.gmail.com>')
            self.messages_by_id[identifier] = canonical.as_bytes()
            if self.mode == "disk_failure":
                self.paths.original_path(identifier).mkdir()
            return {"id": identifier, "labelIds": ["SENT"]}
        return Call(action)


class RunningMailbox:
    def __init__(self, paths, gmail):
        self.paths, self.gmail = paths, gmail
        self.http = None
        self.thread = None
        self.start()

    def start(self):
        from mailbox_app.server import create_app
        self.app = create_app(self.paths)
        self.http = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.paths.config["server_port"] = self.http.server_port
        self.app.config["SERVER_PORT"] = self.http.server_port
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.http.shutdown()
        self.thread.join(timeout=5)
        self.http.server_close()

    def restart(self):
        self.stop()
        self.start()


@pytest.fixture
def live_mailbox(tmp_path, monkeypatch):
    from mailbox_app import send, server, sync
    from mailbox_app.paths import Paths
    paths = Paths({"data_dir": str(tmp_path / "data"), "oauth_client_secrets": str(tmp_path / "never-open-client.json"), "backfill_days": 30, "server_port": 0})
    paths.ensure()
    gmail = GmailCapture(tmp_path, paths)
    monkeypatch.setattr(sync, "build_service", lambda paths: gmail)
    monkeypatch.setattr(send, "build_service", lambda paths: gmail)
    native = []
    monkeypatch.setattr(server, "_open_native", lambda path: native.append(str(path)))
    app = RunningMailbox(paths, gmail)
    app.native = native
    yield app
    app.stop()


@pytest.fixture
def mailbox_page(page, live_mailbox):
    errors = []
    page.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("http://127.0.0.1:") else route.abort())
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_mailbox.url)
    page.locator('.folder-item[data-path="inbox"]').wait_for()
    yield page
    assert not errors, f"Browser JavaScript errors: {errors}"
