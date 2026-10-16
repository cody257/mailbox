"""Opt-in, human-assisted acceptance of the README against real Google Console.

No Google responses are mocked. The operator follows the README in the headed
browser, including login, agreements and permission changes. These assertions
then check the resulting configuration and downloaded file. Never run in CI.
"""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.skipif(
    os.environ.get("MAILBOX_LIVE_SETUP") != "1" or bool(os.environ.get("CI")),
    reason="Live Google setup requires explicit local opt-in and a human operator.",
)


def test_readme_google_setup(browser_type, pytestconfig):
    if pytestconfig.getoption("capture") != "no":
        pytest.fail("Run this interactive check with -s; do not capture account setup.")
    project = input("Dedicated Google Cloud project ID: ").strip()
    account = input("Sole Gmail test-user address: ").strip()
    assert re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project), "Invalid project ID"
    assert account.endswith("@gmail.com"), "Use a dedicated personal Gmail test account"
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf8")
    assert "https://console.cloud.google.com/apis/library/gmail.googleapis.com" in readme
    assert "https://console.cloud.google.com/auth/overview" in readme

    # A fresh context deliberately avoids borrowing a personal browser profile.
    # Do not enable traces, screenshots, video, HAR, or storage-state export here.
    channel = os.environ.get("MAILBOX_SETUP_BROWSER", "chrome")
    browser = browser_type.launch(headless=False, channel=channel)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        page.goto("https://console.cloud.google.com/", wait_until="domcontentloaded")
        print("Follow README step 3 in this browser using only the dedicated account.")
        print("Stop at 'OAuth client created', before clicking Download JSON or OK.")
        input("When that dialog is open, press Enter here: ")
        expect(page.get_by_role("heading", name="OAuth client created", exact=True)).to_be_visible()
        # Arm the real download listener before the operator clicks the button.
        with page.expect_download(timeout=180_000) as pending_download:
            input("Click Download JSON in the browser, then press Enter here: ")
        download = pending_download.value
        assert download.failure() is None, "Google client download failed"
        client_file = Path(download.path())
        try:
            client = json.loads(client_file.read_text(encoding="utf8"))["installed"]
        except (ValueError, KeyError, OSError):
            pytest.fail("Download is not a Desktop OAuth client JSON", pytrace=False)
        valid_client = bool(client.get("client_id")) and bool(client.get("client_secret"))
        correct_project = client.get("project_id") == project
        downloaded_client_id = client.get("client_id")
        # Keep secrets out of assertion output even if a later Console check fails.
        del client
        assert valid_client, "Missing client ID or client secret"
        assert correct_project, "Downloaded client belongs to a different project"
        destination = Path(input("Private destination for client_secret.json (outside checkout): ").strip()).expanduser().resolve()
        repository = Path(__file__).resolve().parents[2]
        assert not destination.is_relative_to(repository), "Store credentials outside the checkout"
        assert not destination.exists(), "Choose a new file; never overwrite existing credentials"
        destination.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(destination)
        page.get_by_role("button", name="OK", exact=True).click()

        query = urlencode({"project": project})
        # Reuse the signed-in account selection from the current browser URL.
        from urllib.parse import parse_qs, urlparse
        authuser = parse_qs(urlparse(page.url).query).get("authuser", ["0"])[0]
        query += "&" + urlencode({"authuser": authuser})

        page.goto(f"https://console.cloud.google.com/apis/dashboard?{query}")
        expect(page.get_by_role("link", name="Gmail API", exact=True)).to_be_visible()
        page.goto(f"https://console.cloud.google.com/auth/audience?{query}")
        main = page.get_by_role("main")
        expect(main.get_by_text("Testing", exact=True)).to_be_visible()
        expect(main.get_by_text("External", exact=True)).to_be_visible()
        expect(main.get_by_role("grid").get_by_role("row")).to_have_count(2)
        assert main.get_by_role("gridcell", name=account, exact=True).count() == 1, "Wrong test user"
        page.goto(f"https://console.cloud.google.com/auth/scopes?{query}")
        main = page.get_by_role("main")
        expect(main.get_by_role("heading", name="Data Access", exact=True)).to_be_visible()
        expect(main.get_by_role("grid")).to_have_count(3)
        # Google renders the heading before the asynchronously loaded scope rows.
        expect(main.get_by_role("gridcell", name=".../auth/gmail.readonly", exact=True)).to_be_visible()
        expect(main.get_by_role("gridcell", name=".../auth/gmail.send", exact=True)).to_be_visible()
        # Inspect every configured row, including non-Gmail/openid permissions.
        actual_scopes = []
        for table in main.get_by_role("grid").all():
            for row in table.get_by_role("row").all()[1:]:
                if row.inner_text().strip() == "No rows to display":
                    continue
                cells = row.get_by_role("gridcell")
                assert cells.count() >= 3, "Unrecognized scope table layout"
                actual_scopes.append(re.sub(r"\s+", "", cells.nth(1).inner_text()))
        assert sorted(actual_scopes) == [".../auth/gmail.readonly", ".../auth/gmail.send"], "Configured scope set differs from the README"
        page.goto(f"https://console.cloud.google.com/auth/clients?{query}")
        client_row = page.get_by_role("row").filter(has_text="Mailbox on my computer")
        expect(client_row).to_have_count(1)
        assert client_row.get_by_role("gridcell", name="Desktop", exact=True).count() == 1, "Wrong OAuth client type"
        client_link = client_row.get_by_role("link", name="Mailbox on my computer", exact=True).get_attribute("href")
        assert urlparse(client_link).path == "/auth/clients/" + downloaded_client_id, "Downloaded JSON does not match the named Desktop client"
        print("PASS: enabled API, Testing/External, sole test user, exact scopes, Desktop client and real JSON download.")
    finally:
        context.close()
        browser.close()
