# Third-party notices

Mailbox is Apache-2.0. Third-party software retains its own terms.

## Bundled browser libraries

- Marked 11.2.0: MIT. Full notice: `mailbox_app/static/vendor/marked.LICENSE`.
- DOMPurify 3.4.17: distributed here under Apache-2.0. Full license: `mailbox_app/static/vendor/DOMPurify.LICENSE`; copyright notices remain in the source.
- Exact source URLs and SHA-256 hashes: `mailbox_app/static/vendor/manifest.json`.

## Python runtime dependencies

These packages are installed separately from upstream distributions; their full license texts accompany those distributions. They are not relicensed by Mailbox. Certifi is unmodified MPL-2.0 software; source is available at https://github.com/certifi/python-certifi. Keep upstream notices when redistributing dependencies.

| Package | Locked version | Upstream license metadata |
|---|---|---|
| beautifulsoup4 | 4.15.0 | MIT License |
| blinker | 1.9.0 | MIT License |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| flask | 3.1.3 | BSD-3-Clause |
| google-api-core | 2.42.0 | Apache 2.0 |
| google-api-python-client | 2.201.0 | Apache 2.0 |
| google-auth | 2.62.0 | Apache 2.0 |
| google-auth-httplib2 | 0.4.4 | Apache 2.0 |
| google-auth-oauthlib | 1.5.0 | Apache 2.0 |
| googleapis-common-protos | 1.75.5 | Apache-2.0 |
| httplib2 | 0.32.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| itsdangerous | 2.2.0 | BSD License |
| jinja2 | 3.1.6 | BSD License |
| markdownify | 1.2.3 | MIT License |
| markupsafe | 3.0.4 | BSD-3-Clause |
| oauthlib | 4.0.0 | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| proto-plus | 1.29.0 | Apache 2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pyasn1-modules | 0.4.2 | BSD |
| pycparser | 3.11 | BSD-3-Clause |
| pyparsing | 3.3.3 | MIT |
| python-frontmatter | 1.3.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| requests-oauthlib | 2.0.0 | ISC |
| six | 1.17.0 | MIT |
| soupsieve | 3.0.3 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| uritemplate | 4.2.0 | BSD 3-Clause OR Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| werkzeug | 3.1.9 | BSD-3-Clause |

Development/test tools are installed separately from `requirements-dev.lock` and are not bundled with the application. Their upstream licenses remain applicable. Changes to runtime dependency versions or license metadata require review of `licenses.json` and this notice.
