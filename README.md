# Mailbox

A local Gmail reader that keeps messages as Markdown, original email files, and attachments. Browse in List or Board view, organize local folders, and compose or reply through Gmail. The files remain readable without Mailbox.

This is a release candidate. Automated checks target Windows, Linux, and macOS; real Gmail and native desktop integration require separate acceptance before a release. There is no hosted service, telemetry, AI routing, background sync, or two-way Gmail-label synchronization.

## Install from a checkout

Use Python **3.11.5 or newer** and a current desktop browser. CI targets the latest 3.11 patch and Python 3.15. On Linux, opening a folder/editor requires a graphical desktop and `xdg-open`; browser-only use does not.

Open a terminal in the downloaded repository:

```text
python -m venv .venv
```

Activate it on Windows PowerShell with `.venv\Scripts\Activate.ps1`, or on Linux/macOS with `source .venv/bin/activate`. If PowerShell activation is restricted, invoke `.venv\Scripts\python.exe` instead of `python` in the commands below.

```text
python -m pip install --only-binary=:all: --require-hashes -r requirements-build.lock
python -m pip install --no-build-isolation --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
```

The Python import package is `mailbox_app`; Python's standard-library `mailbox` module is unaffected. Installation from a built wheel also works outside the checkout; pass an explicit config path when launching there.

## Connect your own Gmail account

Each user supplies their own Google Cloud **Desktop app** OAuth client. Mailbox does not supply a shared OAuth application or collect credentials.

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/), and enable the Gmail API.
2. Configure the Google Auth Platform branding/audience. For a personal External app in Testing, add your own account as a test user.
3. Declare `gmail.readonly` and `gmail.send` access, create an OAuth client with application type **Desktop app**, and download its JSON outside this repository.
4. Copy `config.example.toml` to `config.toml`. Set `data_dir` to a private folder outside the checkout and `oauth_client_secrets` to the downloaded JSON. Paths may use `~`; relative paths resolve beside the config file. Use forward slashes in TOML paths, or escape Windows backslashes.
5. Run the first sync interactively:

```text
python -m mailbox_app.sync --config config.toml
```

The browser opens Google's consent flow. Tokens stay in `<data_dir>/.auth/token.json`. Read access is a restricted Google scope; send access is sensitive. Personal-use exceptions and public-app verification are governed by [Google's verification requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

External apps left in Testing have [seven-day authorization expiry](https://support.google.com/cloud/answer/15549945?hl=en). Plan for reauthorization; do not assume a testing token is permanent. Workspace administrators may also block third-party clients. If Mailbox reports missing permissions after an upgrade, stop the app, remove only its cached token, and repeat authorization for the requested scopes; keep your mail and OAuth client JSON.

## Run and use

```text
python -m mailbox_app.server --config config.toml
```

The browser opens at `http://localhost:5000`. On Windows, `Mailbox.bat` uses the checkout's virtual environment if present. Use `--no-browser` to suppress automatic opening, or `--port 5001` to select another local port.

- **Sync** scans the configured rolling lookback (30 days by default), skips completed messages, and retries incomplete imports. It is not Gmail history/watch synchronization. To fetch an older window, use `python -m mailbox_app.sync --config config.toml --days 365`.
- Create folders and move messages through List or Board view. Only Markdown moves; original email and attachments retain their locations. Local moves never change Gmail labels.
- **Compose/Reply** sends only after an explicit click. A successful delivery is recorded before local mirroring. If the local save fails, retry the local save using the same operation. If delivery is unknown, check Gmail Sent before creating another message; Mailbox never automatically resends that operation.
- The UI can read already-synced files while offline. Sync/send need Google connectivity. Browser assets are bundled locally; remote message images are blocked.

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

- **Missing/invalid config:** copy the example, check TOML quoting, and supply `--config`. No credentials are needed just to read an existing archive.
- **Google authorization:** verify Desktop client type, enabled Gmail API, test-user membership, consent scopes, expiry, and Workspace policy. Follow the reauthorization steps above when prompted.
- **Busy/data write error:** finish the other Mailbox operation; check free space and directory permissions. Keep recovery state and retry sync or local mirroring.
- **Port in use:** use `--port` or change `server_port` in config.
- **Linked paths:** symlinks/junctions inside the mailbox are rejected. Use ordinary folders.
- **Native opening unavailable:** use the browser and open the Markdown folder yourself; configure a Markdown editor/file manager on that desktop.

## Development and release checks

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

Tests use temporary synthetic mail, a real local server/browser, and fake Google transport/native launch boundaries. No test requires Gmail credentials. CI runs Chromium on all three operating systems and Firefox/WebKit rendering checks on Linux. Failure traces contain synthetic data only. Run with `--tracing retain-on-failure --screenshot only-on-failure` to capture local failures.

`uv.lock` and exported hashed requirements pin runtime/test tools; `requirements-build.lock` pins standalone build tools including Cython. Install the build wheels first, then use `--no-build-isolation` so source distributions cannot fetch unpinned build requirements. After an intentional dependency update, regenerate locks, review license metadata and `THIRD_PARTY_NOTICES.md`, audit, and run the suite. Browser asset versions and hashes are recorded in `mailbox_app/static/vendor/manifest.json` and checked before release.

Before recommending a public release, require passing CI for one exact commit, clean history/source/artifact secret scans, and explicit acceptance of real OAuth/refresh/sync/controlled send with a dedicated test account. Verify native opening on each desktop platform separately. Never put real account credentials or mail into CI. Publishing the repository or packages is a separate maintainer action; CI does not publish.

## License

Apache License 2.0; see `LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md`. Python dependencies and bundled browser libraries retain their own licenses.
