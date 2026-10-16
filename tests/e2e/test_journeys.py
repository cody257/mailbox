import hashlib
import json
from email import policy
from email.parser import BytesParser

import frontmatter
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def sync_ui(page):
    with page.expect_response(lambda response: response.url.endswith("/api/sync")) as response:
        page.locator("#sync-btn").click()
    assert response.value.ok
    result = response.value.json()
    expect(page.locator("#sync-btn")).to_be_enabled()
    return result


def compose(page, subject="Composed evidence"):
    page.locator("#compose-btn").click()
    page.locator("#compose-to").fill("bob@example.test")
    page.locator("#compose-cc").fill("copy@example.test")
    page.locator("#compose-subject").fill(subject)
    page.locator("#compose-body").fill("Exact body with unicode: café.\nSecond line.")


def send_ui(page):
    with page.expect_response(lambda response: response.url.endswith("/api/send")) as response:
        page.locator("#compose-send").click()
    return response.value


def test_paginated_sync_dedup_raw_attachment_reader(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    result = sync_ui(page)
    assert result["new"] == 2 and result["failures"] == []
    assert env.gmail.pages == [None, "page-two"]
    expect(page.locator(".message-item")).to_have_count(2)
    for identifier, raw in env.gmail.messages_by_id.items():
        assert env.paths.original_path(identifier).read_bytes() == raw
        path = env.paths.find_md_by_id(identifier)
        assert path and frontmatter.load(path)["message-id"] == f"<{identifier}@example.test>"
    saved = env.paths.attachment_dir("inbox001") / "001-evidence.bin"
    assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(env.gmail.attachment).digest()
    page.locator(".message-item").filter(has_text="Quarterly evidence").click()
    expect(page.locator("#reader")).to_contain_text("Independent fixture body.")
    expect(page.locator("#reader strong")).to_have_text("Bold evidence")
    with page.expect_download() as download:
        page.locator('#reader a').filter(has_text="evidence.bin").click()
    assert open(download.value.path(), "rb").read() == env.gmail.attachment
    first_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in env.paths.data_dir.rglob("*.md")}
    second = sync_ui(page)
    assert second["new"] == 0 and second["skipped"] == 2
    assert env.gmail.fetches == ["inbox001", "inbox002"]
    assert first_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in env.paths.data_dir.rglob("*.md")}


@pytest.mark.rendering
def test_list_nested_folders_context_and_drag_persist(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    sync_ui(page)
    page.once("dialog", lambda dialog: dialog.accept("Projects"))
    page.locator("#new-folder-btn").click()
    parent = page.locator('.folder-item[data-path="Projects"]')
    expect(parent).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept("Nested"))
    parent.locator('[data-action="subfolder"]').click()
    nested = page.locator('.folder-item[data-path="Projects/Nested"]')
    expect(nested).to_be_visible()
    message = page.locator(".message-item").filter(has_text="Quarterly evidence")
    message.click(button="right")
    page.locator("#ctx-menu .ctx-item").filter(has_text="Nested").click()
    expect(message).to_have_count(0)
    page.locator(".message-item").filter(has_text="Second page message").drag_to(parent)
    expect(page.locator(".message-item")).to_have_count(0)
    assert env.paths.find_md_by_id("inbox001").parent == env.paths.data_dir / "Projects" / "Nested"
    assert env.paths.find_md_by_id("inbox002").parent == env.paths.data_dir / "Projects"
    env.restart()
    page.goto(env.url)
    page.locator('.folder-item[data-path="Projects/Nested"]').click()
    expect(page.locator(".message-item")).to_contain_text("Quarterly evidence")
    page.reload()
    page.locator('.folder-item[data-path="Projects"]').click()
    expect(page.locator(".message-item")).to_contain_text("Second page message")


@pytest.mark.rendering
def test_board_drag_reader_and_restart(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    sync_ui(page)
    page.get_by_role("button", name="Board", exact=True).click()
    card = page.locator('.column[data-path="inbox"] .card').filter(has_text="Quarterly evidence")
    card.click()
    expect(page.locator("#modal-root")).to_contain_text("Independent fixture body.")
    page.locator("#reader-close").click()
    card.drag_to(page.locator('.column-body[data-folder="sent"]'))
    expect(page.locator('.column[data-path="sent"] .card')).to_contain_text("Quarterly evidence")
    assert env.paths.find_md_by_id("inbox001").parent == env.paths.sent_dir
    env.restart()
    page.goto(env.url)
    page.get_by_role("button", name="Board", exact=True).click()
    expect(page.locator('.column[data-path="sent"] .card')).to_contain_text("Quarterly evidence")
    assert sync_ui(page)["new"] == 0
    assert len(list(env.paths.data_dir.rglob("*quarterly*.md"))) == 1


def test_compose_and_reply_exact_mime_and_single_mirror(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    sync_ui(page)
    compose(page)
    assert send_ui(page).ok
    expect(page.locator("#compose-send")).to_have_count(0)
    first = BytesParser(policy=policy.default).parsebytes(env.gmail.sent[0])
    assert str(first["From"]) == "owner@example.test"
    assert str(first["To"]) == "bob@example.test"
    assert str(first["Cc"]) == "copy@example.test"
    assert str(first["Subject"]) == "Composed evidence"
    assert first.get_content() == "Exact body with unicode: café.\nSecond line."
    assert first["In-Reply-To"] is None
    page.locator(".message-item").filter(has_text="Quarterly evidence").click()
    page.locator("#reply-btn").click()
    expect(page.locator("#compose-to")).to_have_value("alice@example.test")
    expect(page.locator("#compose-subject")).to_have_value("Re: Quarterly evidence")
    page.locator("#compose-body").fill("Independent reply text.")
    assert send_ui(page).ok
    expect(page.locator("#compose-send")).to_have_count(0)
    reply = BytesParser(policy=policy.default).parsebytes(env.gmail.sent[1])
    assert reply["In-Reply-To"] == "<inbox001@example.test>"
    assert reply["References"] == "<ancestor@example.test> <inbox001@example.test>"
    assert reply.get_content() == "Independent reply text."
    assert len(list(env.paths.sent_dir.glob("*.md"))) == 2
    assert env.paths.original_path("sent0001").read_bytes() == env.gmail.sent[0]
    assert env.paths.original_path("sent0002").read_bytes() == env.gmail.sent[1]
    assert json.loads((env.gmail.root / "capture.json").read_text())["calls"] == 2


def test_sync_interrupted_attachment_write_recovers_without_duplicates(mailbox_page, live_mailbox):
    page, env = mailbox_page, live_mailbox
    blocked = env.paths.attachment_dir("inbox001")
    blocked.write_text("actual filesystem obstruction")
    result = sync_ui(page)
    assert len(result["failures"]) == 1
    blocked.unlink()
    result = sync_ui(page)
    assert result["new"] == 1 and not result["failures"]
    assert (blocked / "001-evidence.bin").read_bytes() == env.gmail.attachment
    assert len(list(env.paths.data_dir.rglob("*quarterly*.md"))) == 1
