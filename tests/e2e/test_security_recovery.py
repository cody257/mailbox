import threading

import pytest
from playwright.sync_api import expect
from werkzeug.serving import make_server

from conftest import raw_message
from test_journeys import compose, send_ui, sync_ui

pytestmark = pytest.mark.e2e


def test_compose_does_not_steal_focus_after_user_selects_subject(mailbox_page):
    page = mailbox_page
    # Hold deferred UI work until after the user selects another field.
    page.evaluate('''() => {
        window.deferredCallbacks = [];
        const original = window.setTimeout;
        window.setTimeout = (callback, delay, ...args) => {
            if (typeof callback === 'function' && delay <= 100) {
                window.deferredCallbacks.push(() => callback(...args));
                return 0;
            }
            return original(callback, delay, ...args);
        };
    }''')
    page.locator('#compose-btn').click()
    page.locator('#compose-subject').focus()
    page.evaluate('() => window.deferredCallbacks.forEach(callback => callback())')
    expect(page.locator('#compose-subject')).to_be_focused()


def test_accepted_send_refresh_failure_does_not_become_delivery_failure(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    compose(page, 'Sent before server restart')
    def expire_token_before_refresh(route):
        # Keep the listening port while reproducing restart's token rotation;
        # the real server, rather than a mocked response, rejects this request.
        env.app.config['MAILBOX_TOKEN'] = 'rotated-synthetic-session'
        route.continue_()
    page.route('**/api/folders', expire_token_before_refresh)
    with page.expect_response(lambda response: response.url.endswith('/api/folders')) as refresh:
        assert send_ui(page).json()['status'] == 'sent'
    assert refresh.value.status == 403
    expect(page.locator('#compose-send')).to_have_count(0)
    expect(page.locator('.toast.error')).to_contain_text('Sent, but')
    assert len(env.gmail.sent) == 1


def test_late_send_response_does_not_close_a_replacement_dialog(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    compose(page, 'Pending dialog')
    held = []
    page.route('**/api/send', lambda route: held.append(route))
    with page.expect_request(lambda request: request.url.endswith('/api/send')):
        page.locator('#compose-send').click()
    page.locator('#compose-close').click()
    page.locator('#compose-btn').click()
    expect(page.locator('#compose-subject')).to_have_value('Pending dialog')
    with page.expect_response(lambda response: response.url.endswith('/api/send')):
        held.pop().continue_()
    # Wait for post-send refresh, proving the original handler has completed.
    expect(page.locator('.toast')).to_contain_text('Sent.')
    expect(page.locator('#compose-subject')).to_have_value('Pending dialog')
    assert len(env.gmail.sent) == 1


def test_partial_sync_shows_failure_and_retry_guidance(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    # A real filesystem obstruction fails one import; the others still work.
    blocked_id = next(iter(env.gmail.messages_by_id))
    env.paths.original_path(blocked_id).mkdir()
    with page.expect_response(lambda response: response.url.endswith('/api/sync')) as response:
        page.locator('#sync-btn').click()
    assert len(response.value.json()['failures']) == 1
    expect(page.locator('#status')).to_contain_text('1 failed')
    expect(page.locator('.toast.error')).to_contain_text('Retry Sync')
    expect(page.locator('#sync-btn')).to_be_enabled()


def browser_post(page, route, payload):
    return page.evaluate("""async ({route, payload}) => {
        const response = await fetch(route, {method: 'POST', headers: {
            'Content-Type': 'application/json',
            'X-Mailbox-Token': document.querySelector('meta[name="mailbox-token"]').content
        }, body: JSON.stringify(payload)});
        return {status: response.status, data: await response.json()};
    }""", {"route": route, "payload": payload})


def test_accepted_send_disk_failure_retries_mirror_only(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    env.gmail.mode = "disk_failure"
    compose(page, "Accepted but not mirrored")
    with page.expect_request(lambda request: request.url.endswith("/api/send")) as request:
        response = send_ui(page)
    assert response.json()["status"] == "sent_unmirrored"
    expect(page.locator("#compose-send")).to_have_text("Retry local save")
    expect(page.locator("#compose-status")).to_contain_text("accepted")
    assert len(env.gmail.sent) == 1
    env.paths.original_path("sent0001").rmdir()
    env.gmail.mode = "success"
    assert send_ui(page).json()["status"] == "sent"
    expect(page.locator("#compose-send")).to_have_count(0)
    env.restart()
    page.goto(env.url)
    result = browser_post(page, "/api/send", request.value.post_data_json)
    assert result["data"]["status"] == "sent"
    assert len(env.gmail.sent) == 1
    assert env.paths.original_path("sent0001").read_bytes() == env.gmail.sent[0]
    assert len(list(env.paths.sent_dir.glob("*.md"))) == 1
    repeated = browser_post(page, "/api/send", request.value.post_data_json)
    assert repeated["data"]["status"] == "sent"
    assert len(env.gmail.sent) == 1


def test_unknown_send_is_visible_and_same_operation_cannot_resend(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    env.gmail.mode = "unknown"
    compose(page, "Unknown delivery")
    with page.expect_request(lambda request: request.url.endswith("/api/send")) as request:
        response = send_ui(page)
    assert response.json()["status"] == "unknown"
    expect(page.locator("#compose-send")).to_be_disabled()
    expect(page.locator("#compose-status")).to_contain_text("Gmail")
    assert len(env.gmail.sent) == 1
    assert not list(env.paths.sent_dir.glob("*.md"))
    page.reload()
    page.locator("#compose-btn").click()
    expect(page.locator("#compose-subject")).to_have_value("Unknown delivery")
    expect(page.locator("#compose-subject")).to_be_disabled()
    with page.expect_request(lambda outgoing: outgoing.url.endswith("/api/send")) as retry:
        assert send_ui(page).json()["status"] == "unknown"
    assert retry.value.post_data_json == request.value.post_data_json
    assert len(env.gmail.sent) == 1
    env.restart()
    page.goto(env.url)
    result = browser_post(page, "/api/send", request.value.post_data_json)
    assert result["data"]["status"] == "unknown"
    assert len(env.gmail.sent) == 1


def test_rejected_send_preserves_draft_and_never_claims_sent(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    env.gmail.mode = "rejected"
    compose(page, "Rejected delivery")
    with page.expect_request(lambda request: request.url.endswith("/api/send")) as request:
        response = send_ui(page)
    assert response.json()["status"] == "rejected"
    expect(page.locator("#compose-send")).to_be_enabled()
    expect(page.locator("#compose-subject")).to_have_value("Rejected delivery")
    assert not list(env.paths.sent_dir.glob("*.md"))
    result = browser_post(page, "/api/send", request.value.post_data_json)
    assert result["data"]["status"] == "rejected"
    assert len(env.gmail.sent) == 1


@pytest.mark.rendering
def test_mail_content_cannot_execute_or_request_remote_resources(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    malicious = '''Visible hostile fixture.
<script>window.__mailbox_pwned = true</script>
<img src="https://tracker.invalid/pixel" onerror="window.__mailbox_pwned=true">
![remote image](https://tracker.invalid/markdown-pixel)
<iframe src="https://tracker.invalid/frame"></iframe>
<svg onload="window.__mailbox_pwned=true"></svg>
[bad link](javascript:window.__mailbox_pwned=true)
<form action="https://tracker.invalid/submit"><input autofocus onfocus="window.__mailbox_pwned=true"></form>
'''
    env.gmail.messages_by_id["hostile01"] = raw_message("Hostile <img src=x onerror=alert(1)>", "hostile01", malicious)
    external = []
    page.on("request", lambda request: external.append(request.url) if not request.url.startswith(env.url + "/") else None)
    page.reload()
    sync_ui(page)
    page.locator(".message-item").filter(has_text="Hostile").click()
    expect(page.locator("#reader")).to_contain_text("Visible hostile fixture.")
    assert page.evaluate("window.__mailbox_pwned === undefined")
    assert page.locator('#reader script, #reader iframe, #reader img[src^="http"], #reader [onerror], #reader [onload], #reader a[href^="javascript:"]').count() == 0
    page.get_by_role("button", name="Board", exact=True).click()
    page.locator(".card").filter(has_text="Hostile").click()
    expect(page.locator("#modal-root")).to_contain_text("Visible hostile fixture.")
    assert page.evaluate("window.__mailbox_pwned === undefined")
    assert external == []


def test_hostile_origin_form_cannot_mutate(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    html = f'''<form method="POST" action="{env.url}/api/folders" enctype="text/plain">
        <input name='{{"name":"intruder","extra":"' value='"}}'><button>Attack</button></form>'''.encode()
    def attacker(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/html")])
        return [html]
    hostile = make_server("127.0.0.1", 0, attacker, threaded=True)
    thread = threading.Thread(target=hostile.serve_forever, daemon=True)
    thread.start()
    try:
        page.goto(f"http://127.0.0.1:{hostile.server_port}")
        with page.expect_response(lambda response: response.url.endswith("/api/folders")) as response:
            page.get_by_role("button", name="Attack").click()
        assert response.value.status in (400, 403, 415)
        assert not (env.paths.data_dir / "intruder").exists()
    finally:
        hostile.shutdown()
        thread.join(timeout=5)
        hostile.server_close()


def test_traversal_private_files_and_native_execution_blocked(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    sync_ui(page)
    secret = env.paths.auth_dir / "canary.txt"
    secret.write_text("SECRET-CANARY-MUST-NOT-LEAK")
    token = page.locator('meta[name="mailbox-token"]').get_attribute("content")
    for path in ["/api/attachment/../.auth/canary.txt", "/api/attachment/%2e%2e/.auth/canary.txt", "/api/attachment/inbox001/../../.auth/canary.txt", "/api/message?path=.auth/canary.txt", "/api/messages?folder=.auth"]:
        response = page.request.get(env.url + path, headers={"X-Mailbox-Token": token})
        assert response.status in (400, 403, 404)
        assert "SECRET-CANARY" not in response.text()
    executable = env.paths.inbox_dir / "danger.exe"
    executable.write_bytes(b"MZ synthetic, never execute")
    result = browser_post(page, "/api/open-file", {"path": "inbox/danger.exe"})
    assert result["status"] in (400, 403)
    assert env.native == []
    page.locator('.folder-item[data-path="inbox"] [data-action="open"]').click()
    expect(page.locator("#folder-list")).to_be_visible()
    # Wait on the actual response rather than assuming click completion means IO completion.
    result = browser_post(page, "/api/open-folder", {"path": "inbox"})
    assert result["status"] == 200
    assert env.native and set(env.native) == {str(env.paths.inbox_dir.resolve())}


def test_host_token_origin_and_content_type_are_independent_guards(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    token = page.locator('meta[name="mailbox-token"]').get_attribute("content")
    assert page.request.get(env.url + "/api/folders").status == 403
    assert page.request.get(env.url + "/", headers={"Host": "attacker.invalid"}).status == 403
    cases = [
        {"Origin": env.url, "Content-Type": "application/json"},
        {"X-Mailbox-Token": token, "Origin": "https://attacker.invalid", "Content-Type": "application/json"},
        {"X-Mailbox-Token": token, "Content-Type": "application/json"},
        {"X-Mailbox-Token": token, "Origin": env.url, "Content-Type": "text/plain"},
    ]
    for headers in cases:
        response = page.request.post(env.url + "/api/folders", headers=headers, data='{"name":"forbidden"}')
        assert response.status in (403, 415)
    assert not (env.paths.data_dir / "forbidden").exists()
    assert browser_post(page, "/api/folders", {"name": "allowed"})["status"] == 200
    assert (env.paths.data_dir / "allowed").is_dir()
