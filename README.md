# Mailbox

A Gmail reader that runs on your computer and saves your mail as readable text files, original emails, and attachments. Browse a list or a board, organize folders, and send or reply through Gmail. Your saved files remain usable without Mailbox.

Mailbox is built for working with AI agents. Instead of giving each AI app its own Gmail connection, you get one folder of plain text that Codex, Claude Code, opencode, or any agent that reads files can search, summarize, and build on. See [Use with AI agents](#use-with-ai-agents).

This is a **Windows beta**. Real Gmail send/reply, sync, token refresh, reauthorization, and Windows file/folder opening have been checked. Core automated tests also cover Linux and macOS, but native desktop integration on those platforms remains unverified. Mailbox does not upload your archive to a separate hosted service or send usage analytics.

## Start here

You do not need Git or programming experience, but this version still requires some setup: install Python, copy a few commands, and give Mailbox permission to use Gmail. There is no one-click installer yet. A spare Gmail account is a good choice for a first try.

The numbered steps below use **Windows**. Mac/Linux differences are included in step 2. Keep this page open as you work.

## 1. Download Mailbox

1. Open the [Mailbox project page](https://github.com/cody257/mailbox).
2. Click the green **Code** button, then **Download ZIP**. You do not need GitHub Desktop or Git.
3. In Downloads, right-click the ZIP and choose **Extract All**. Open the extracted folder, then the folder inside it if needed.
4. You are in the right folder when you can see `README.md`, `config.example.toml`, and `Mailbox.bat`. Keep this folder; it contains the app.

You do not need a GitHub account to download the ZIP.

## 2. Install Mailbox

Python is the program that runs Mailbox. Install a current stable Python from [python.org/downloads](https://www.python.org/downloads/) if you do not already have it. Mailbox needs Python **3.11.5 or newer**; Python 3.15 is tested.

In File Explorer, open the extracted Mailbox folder. Click the address bar at the top, type `powershell`, and press Enter. A command window opens in that folder.

Copy this line into the window and press Enter:

```powershell
python --version
```

You should see a Python version number. If Windows opens the Microsoft Store or says Python cannot be found, finish installing Python, close the command window, and open it again before continuing.

Run each of these lines separately. Wait until the prompt returns before running the next one. Installation prints a lot of text; if a line ends with an error, stop there and use the troubleshooting section below.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

These commands create a private Python environment for Mailbox and install its required libraries. You do not need to activate the environment. Keep this command window open for later steps.

<details>
<summary>Mac or Linux installation</summary>

Extract the ZIP. Open Terminal, type `cd ` (including the space), drag the extracted Mailbox folder into Terminal, and press Enter. On systems without folder dragging, use `cd "/full/path/to/mailbox-main"`, replacing the path with yours.

Check `python3 --version` is at least 3.11.5, then run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-build.lock
.venv/bin/python -m pip install --no-build-isolation --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

If Linux reports that `venv` is missing, install your distribution's Python venv package, then retry the first command. In later steps, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`. Open/edit files using your file manager and text editor. The Google setup is the same on all platforms. Linux desktop opening also needs `xdg-open` and a graphical desktop.

</details>

## 3. Give Mailbox permission to use Gmail

Mailbox uses Google's sign-in process, not your Gmail password. You create a personal connection in Google's settings, then download a small settings file. This is called an **OAuth client**.

### Open Google Console and create a project

Open [Google Cloud Console](https://console.cloud.google.com/) in your normal Chrome, Edge, Firefox, or Safari browser. If clicking does nothing or opens an unusable in-app browser, copy this address into your browser's address bar:

```text
https://console.cloud.google.com/
```

Sign in with the Google account you want to use for this setup. Check the profile picture in the top-right corner to confirm the account. If Google shows a sign-in page, that is expected.

1. At the top, open the project selector (it may say **Select a project** or show another project's name).
2. Choose **New project**, name it `My Mailbox`, and click **Create**. Select that project when creation finishes.
3. Open the [Gmail API page directly](https://console.cloud.google.com/apis/library/gmail.googleapis.com). Check that `My Mailbox` is still selected at the top, then click **Enable**. If it says **Manage**, this step is already complete.

### Add your Gmail account and permissions

1. Open [Google Auth setup directly](https://console.cloud.google.com/auth/overview), and check that `My Mailbox` is selected. The menu route is **☰ → APIs & Services → OAuth consent screen**; Google Auth Platform may not appear in the main menu. On **OAuth Overview**, click **Get started** when it says **Google Auth Platform not configured yet**.
2. Complete the four sections of **Project configuration** in order:
   - **App Information:** enter `My Mailbox` for **App name**, choose your address from the **User support email** dropdown, and click **Next**.
   - **Audience:** choose **External** (personal Gmail accounts cannot use Internal), then click **Next**. This starts in Testing mode; it does not make your app public.
   - **Contact Information:** enter your email in **Email addresses**, then click **Next**.
   - **Finish:** read Google's linked User Data Policy. If you agree, select its checkbox, click **Continue**, then **Create** at the bottom.
3. Open **Audience**. Check that publishing status is **Testing**. Under **Test users**, click **Add users**, enter the Gmail address you will read/send with, and press **Enter** so it becomes a selected email entry. Click **Save**. Your address should now appear in the test-user table.
4. Open **Data Access**, then **Add or remove scopes**. In the panel, scroll to **Manually add scopes**. Paste these two lines into **Manually paste scopes**:

   ```text
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/gmail.send
   ```

   Click **Add to table**, wait for both entries to appear, then click **Update**. Back on the Data Access page, click **Save**. You should see `gmail.send` under sensitive scopes and `gmail.readonly` under restricted scopes, with no other selected permissions.

Google calls permissions “scopes.” These allow reading/downloading mail and sending when you click Send. See [Google's consent setup guide](https://developers.google.com/workspace/guides/configure-oauth-consent) if the screen layout differs.

### Download your connection file

1. In **Google Auth Platform → Clients**, choose **Create client**.
2. Select **Desktop app**, name it `Mailbox on my computer`, leave **Use this client for an AI-powered agent** unchecked, and click **Create**.
3. In **OAuth client created**, click **Download JSON**. Confirm the file has actually reached your browser's Downloads folder **before clicking OK**. Google no longer lets you download that secret again after closing this dialog. Use your normal desktop browser for this step; an embedded/in-app browser may not save the download. [Google's desktop-client instructions](https://developers.google.com/workspace/gmail/api/quickstart/python#authorize_credentials_for_a_desktop_application) describe these screens.
4. In File Explorer, enter `%USERPROFILE%` in the address bar. Create a folder named `Mailbox-private` here, outside the downloaded app folder. Move the JSON file into it and rename it `client_secret.json`. Turn on **View → Show → File name extensions** to check it is not named `client_secret.json.json` or `.txt`.

On Mac/Linux, create `Mailbox-private` in your home folder instead. Keep this file on your computer; do not post it on GitHub or paste it into a chat.

**If you closed the dialog before saving the download:** open **Clients → Mailbox on my computer** in your normal browser and maximize the window. Under **Additional information** on the right, find **Client secrets**. Click **+ Add secret** below **Status: Enabled**, then download the new JSON before closing its dialog. Once you have the file, continue with step 4 above.

## 4. Tell Mailbox where to save your mail

In the extracted app folder, make a copy of `config.example.toml` and name the copy `config.toml`. Open the copy with Notepad (right-click → **Open with → Notepad**). Replace its contents with:

```toml
data_dir = "~/Mailbox-private/mail"
oauth_client_secrets = "~/Mailbox-private/client_secret.json"
backfill_days = 30
server_port = 5000
```

Save and close Notepad. Check the filename is exactly `config.toml`, not `config.toml.txt`. The `~` means your home folder; you do not need to replace it with your name. Mailbox creates the `mail` subfolder automatically. Use a new folder for a different Gmail account.

## 5. Sign in, download mail, and open Mailbox

In the command window from step 2, run:

```powershell
.\.venv\Scripts\python.exe -m mailbox_app.sync --config config.toml
```

A Google sign-in page opens in your browser. Select the Gmail address you added as a test user and review the requested read/send permissions. Grant both to continue. The command downloads messages from the last 30 days; wait until it finishes. An empty new account may have no messages to download.

If Google shows an unverified-app warning, check that the app name is the personal client you just created before deciding whether to continue. If access is blocked, check the troubleshooting section; do not change unrelated browser security settings.

Then run:

```powershell
.\.venv\Scripts\python.exe -m mailbox_app.server --config config.toml
```

Mailbox opens in your browser. If it does not, type `http://localhost:5000` into the address bar. This address is the app running on your own computer. Leave the command window open while using it. To stop, click the command window and press **Ctrl+C**.

**Next time on Windows:** double-click `Mailbox.bat` in the app folder. On Mac/Linux, open Terminal in the app folder and run `.venv/bin/python -m mailbox_app.server --config config.toml`. Refresh the browser tab after restarting Mailbox.

## Everyday use

- Click **Sync** to download recent mail. It checks the last 30 days each time, skips completed downloads, and retries incomplete ones. It does not update automatically in the background.
- Create folders and move messages through List or Board view. Only Markdown moves; original email and attachments retain their locations. Local moves never change Gmail labels.
- **Compose/Reply** sends only after an explicit click. A successful delivery is recorded before local mirroring. If the local save fails, retry the local save using the same operation. If delivery is unknown, check Gmail Sent before creating another message; Mailbox never automatically resends that operation.
- The UI can read already-synced files while offline. Sync/send need Google connectivity. Browser assets are bundled locally; remote message images are blocked.

Google connections left in Testing have [seven-day authorization expiry](https://support.google.com/cloud/answer/15549945?hl=en), so you may need to sign in again. See **Expired/revoked sign-in** under Troubleshooting when this happens. A work/school account may require your administrator's approval.

## Use with AI agents

Open your agent in the `Mailbox-private` folder and ask it about your mail. Compared with connecting Gmail to each AI app:

- One archive works with every agent, so you are not tied to one vendor's plugin.
- Agents can search everything at once and follow whole threads. Each Markdown file starts with `from`, `to`, `cc`, `date`, `subject`, `in-reply-to`, and `references`.
- You own the Google connection, and Mailbox sends only when you click Send.
- Synced mail stays readable offline and without Mailbox.

Let agents read `inbox/`, `sent/`, and your own folders. Keep them away from `client_secret.json` and `mail/.auth/`: the token there can read and send your Gmail. Treat `.state/` and `.originals/` as read-only. If your agent uses a cloud model, the messages it reads are sent to that model's provider; use a local model if mail must stay on your computer.

### Build a knowledge base from your inbox

This follows Andrej Karpathy's "LLM wiki" idea: an agent reads raw sources and keeps a set of linked pages about them up to date. Mailbox is the raw source. Keep the agent's pages beside the mail folder, not inside it, so they do not appear as Mailbox folders:

```text
Mailbox-private/
  mail/          Mailbox data_dir; agents read, never edit
  wiki/          Pages the agent writes and maintains
    AGENTS.md    Your rules: page formats, linking, never edit ../mail
    people/
    projects/
    log.md       What the agent processed and when
```

After each sync, ask the agent to read new messages, update the relevant pages with links back to the source Markdown files, and add an entry to `log.md`. Codex and opencode read `AGENTS.md`; for Claude Code, add a `CLAUDE.md` that points to it.

## Data and security

```text
<data_dir>/
  .auth/token.json               OAuth token; keep private
  .state/                        Import/send recovery records; contains mail
  .originals/<gmail-id>.eml       Original email backups
  attachments/<gmail-id>/        Safe, unique attachment filenames
  inbox/                         New received Markdown messages
  sent/                          Sent Markdown messages
  <your folders>/                Locally organized messages
```

Back up the entire data directory privately. Do not delete `.state` to clear a send error: it prevents replay of uncertain sends. Existing messages and moved notes are preserved during recovery. Journaling handles ordinary process interruption; sudden power loss or storage failure can still lose local state, particularly on Windows, so check Gmail Sent before manually retrying after such an event. Old Markdown remains readable after the HTML converter change; new imports may have different Markdown formatting.

Mail is stored as plain files, not encrypted by Mailbox. Use an account-private directory, OS permissions and disk encryption as needed. Do not put it in a public/synchronized share or the source checkout. Ignore rules are a safeguard, not a substitute for inspecting staged files.

The server binds only to loopback with debugging disabled. It checks Host/Origin and per-run API tokens, restricts paths, and blocks remote content. Reload the UI after restarting the server because its API token changes. Never expose it through a reverse proxy, port forwarding, or a public listener. Native file opening is limited to local Markdown messages and user folders; attachments download rather than execute.

## Troubleshooting

- **A Google Console link will not open:** copy `https://console.cloud.google.com/` into a normal browser, sign in, and select `My Mailbox`. If Google shows an error, keep its exact wording; the remedy depends on the error. Never include the downloaded JSON or token file in a help request.
- **The command cannot find a file, or says this is not a Python project:** open PowerShell inside the extracted folder that contains `README.md` and `pyproject.toml`. Do not run from inside the ZIP.
- **Python cannot be found:** install Python, then close and reopen PowerShell. If `py --version` works, use `py` instead of `python` for the version check and the first `-m venv` command. Keep the later `.venv` commands unchanged.
- **Missing/invalid config:** check `config.toml` is beside `README.md`, has no extra `.txt` extension, and matches step 4, including quotes. No credentials are needed just to read an existing archive.
- **Client credentials file missing:** confirm `client_secret.json` is in the `Mailbox-private` folder in your home directory. A downloaded file still named `client_secret_123...json` needs renaming to match the setting.
- **Google says access is blocked or the app is only available to test users:** select the correct Google Cloud project, add the exact Gmail address under **Audience → Test users**, and use that account during sign-in. Also check the Gmail API is enabled and the client type is **Desktop app**.
- **Expired/revoked sign-in or missing permissions:** stop Mailbox, then repeat the sync command in step 5. If the same error persists, remove only `mail/.auth/token.json` inside `Mailbox-private`, then repeat sign-in. Keep `client_secret.json`, `.state`, and all mail. Select both requested permissions. Work/school restrictions may require an administrator.
- **Busy/data write error:** finish the other Mailbox operation; check free space and directory permissions. Keep recovery state and retry sync or local mirroring.
- **Port in use:** use `--port` or change `server_port` in config.
- **Linked paths:** symlinks/junctions inside the mailbox are rejected. Use ordinary folders.
- **Native opening unavailable:** use the browser and open the Markdown folder yourself; configure a Markdown editor/file manager on that desktop.

## Advanced settings

Paths in `config.toml` may use `~`. Relative paths resolve beside the config file. Use forward slashes inside quoted paths, including on Windows. To import a longer window, add `--days 365` to the sync command. To use another local port, add `--port 5001` to the server command and open `http://localhost:5001`. `--no-browser` suppresses automatic browser opening.

The Python package is `mailbox_app`; Python's standard-library `mailbox` module is unaffected. A built wheel works outside the checkout with an explicit `--config` path. Reading Gmail is a restricted Google scope; sending is sensitive. Each user owns their Desktop OAuth client. See [Google's verification requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) before distributing a shared OAuth application.

## Development and release checks

<details>
<summary>For contributors: tests, packages, and release requirements</summary>

The commands below assume an activated virtual environment. Alternatively, replace `python` with `.\.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Mac/Linux, as in the setup instructions.

```text
python -m pip install --only-binary=:all: --require-hashes -r requirements-build.lock
python -m pip install --no-build-isolation --require-hashes -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m playwright install chromium
python -m pytest --browser chromium
python -m build --no-isolation
python scripts/check_release.py
python -m pip_audit --require-hashes --disable-pip -r requirements-dev.lock
python scripts/audit_js.py
```

The default tests use temporary synthetic mail, a real local server/browser, and fake Google transport/native launch boundaries. They require no Gmail credentials. CI runs Chromium on all three operating systems and Firefox/WebKit rendering checks on Linux. Failure traces contain synthetic data only. Run with `--tracing retain-on-failure --screenshot only-on-failure` to capture local failures.

Google Console setup needs a separate, human-assisted live acceptance run; CI does not validate Google's signed-in screens. With Chrome installed, run this from PowerShell after installing the development dependencies:

```powershell
$env:MAILBOX_LIVE_SETUP = "1"
.\.venv\Scripts\python.exe -m pytest tests/live/test_google_setup.py -s -q
Remove-Item Env:MAILBOX_LIVE_SETUP
```

Use a dedicated test account/project and follow the README in the browser it opens. The test checks enabled Gmail API, External/Testing, exactly one test user, exactly the two scopes, a Desktop client, and an actual valid JSON download. It leaves login, agreements, and permission changes to the operator, and does not read or send Gmail messages. It saves the downloaded file only to a new private path you provide outside the checkout. Do not enable traces, screenshots, recordings, or `--showlocals` for this live run. This test is skipped by default and always skipped in CI; a skip is not acceptance evidence.

`uv.lock` and exported hashed requirements pin runtime/test tools; `requirements-build.lock` pins standalone build tools including Cython. Install the build wheels first, then use `--no-build-isolation` so source distributions cannot fetch unpinned build requirements. After an intentional dependency update, regenerate locks, review license metadata and `THIRD_PARTY_NOTICES.md`, audit, and run the suite. Browser asset versions and hashes are recorded in `mailbox_app/static/vendor/manifest.json` and checked before release.

Before recommending a public release, require passing CI for one exact commit, clean history/source/artifact secret scans, and explicit acceptance of real OAuth/refresh/sync/controlled send with a dedicated test account. Verify native opening on each desktop platform separately. Never put real account credentials or mail into CI. Publishing the repository or packages is a separate maintainer action; CI does not publish.

</details>

## License

Apache License 2.0; see `LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md`. Python dependencies and bundled browser libraries retain their own licenses.
