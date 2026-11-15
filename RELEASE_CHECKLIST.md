# Release preparation and publication gate

Prepared for PoriaLinkBot's independent portfolio release v1.0.0. The owner has
authorized commit/push and an annotated release tag, and explicitly accepts the
visibility of the existing Git author email. Git references are authoritative
for the final publication result; this document records the pre-push review.
No deployment, history rewrite, rebranding, or license addition is authorized. This report intentionally contains no real user,
administrator, token, email-address, or remote-owner values.

## Release gate

| Gate | Result | Evidence / remaining action |
| --- | --- | --- |
| A. Source-code security | PASS | No credential-pattern matches found; authorization, ownership, CSV safety, generic errors, and atomic click behavior remain covered by tests |
| B. Git history safety | PASS | Owner explicitly accepted existing author-email visibility; existing authors/history remain unchanged |
| C. Dependency compatibility | WARNING | CPython 3.14.4, installed runtime pins, offline resolver check, and pip check pass; fresh installation and live vulnerability auditing remain unperformed |
| D. Automated tests | PASS | 207 tests pass in an isolated working directory; no Telegram API calls |
| E. Documentation | PASS | README, deployment handoff, safe environment template, screenshot plan, release guidance, and local document links prepared |
| F. Production configuration | PASS | Reviewed offline: explicit production HTTPS origin, absolute database path, debug off, private admin model; actual infrastructure remains undeployed |
| G. Organization handoff | WARNING | Independent-instance deployment is documented; actual organization branding, identities, ownership, privacy/retention, infrastructure, and load testing are future decisions |
| H. Public GitHub publication | PASS | Owner authorized publication to the existing public repository; known critical checks pass, with the dependency/audit limitations below |

PASS means the stated offline check passed, not a security certification or proof
of production operation. The former author-email privacy blocker is resolved by
explicit owner acceptance. The authorized release procedure must still pass its
final tests, staged-file review, and remote checks before pushing.

## Secret, privacy, and repository audit

- Scanned 9 reachable commits / 58 reachable historical blobs using redacted
  credential-pattern checks. Expanded the scan to all 116 local Git blob objects
  (including objects not necessarily reachable from current refs); no token,
  private-key, AWS access-key, or GitHub-token patterns matched.
- No reachable historical database, `.env`, CSV, log, private-key, or virtualenv
  artifacts were identified. No tracked runtime/secret artifacts were found.
- Working source/configuration/documentation scans found no credential-pattern,
  non-example email, or developer-home-path matches. Local Git configuration was
  inspected without printing values; no embedded HTTP credentials were identified.
- A real-looking author email is embedded in commit metadata. It is not printed
  here. Changing future `user.email` settings does **not** remove past metadata.
  The owner explicitly accepts its public visibility for this release. Existing
  commit authors and history are preserved; no configuration change or credential
  rotation is part of publication.
- Local SQLite data/backup files contain application records. They remain ignored
  and untouched and were absent from reachable history. No private rows were
  printed. Do not copy them, journals, or generated reports into the publication.
- The local virtualenv/caches and agent/cloud/editor directories are excluded.
  `.env.example` is an allowed safe template; actual `.env` variants remain ignored.
- Existing `origin` and branch `main` are preserved. A local fingerprint check
  verifies branch/configuration were not changed during preparation. Release
  staging is limited to the reviewed source/documentation/test manifest.

This is a heuristic audit, not an exhaustive secret-detection guarantee.
`gitleaks`, `trufflehog`, and `pip-audit` were unavailable. No dedicated detector,
entropy scan, image-metadata audit of future screenshots, external credential
validity check, or vulnerability-advisory scan has been claimed. No live Telegram
requests were made. Git author metadata is distinct from source-code secrets.

## Dependency verification

`requirements.txt` includes the runtime libraries; `requirements.lock` pins their
installed transitive closure without adding development tools. The current
environment passes `pip check` and `pip install --dry-run --no-index -r
requirements.lock`. This validates the already installed environment, **not** a
fresh download/install on another machine. Current dependency metadata requires
Python >=3.10 collectively; the release run only tested 3.14.4.

Before production deployment, use a separate audit environment and reviewed
network access to run a current advisory audit (for example `pip-audit -r
requirements.lock`) and a clean installation/full suite. Do not add audit tools
to application runtime requirements or assume that an absent scanner means safe
dependencies. Audit the separately chosen WSGI server/infrastructure lock too.
Upstream deployment documentation was consulted; that is not a package audit or
execution of the deployment examples.

## Focused security review

- Protected commands/callbacks use `@admin_only`, authenticated Telegram sender
  IDs, and private-chat checks; missing/invalid allowlists fail closed.
- Owned links are queried by authenticated Telegram identity, with no supplied
  owner override and no public analytics. Flask exposes no admin/report API or
  repository-file serving route. Offline Flask checks returned 404 for `.env`,
  database, requirements, deployment-document, and `.git/config` URL paths.
- CSVs are private documents, bounded to 5,000 rows / 8 MiB, formula-neutralized,
  and cleaned up; named InputFile delivery and all date ranges are tested.
- URL validation, case-insensitive uniqueness/lookup, collision retry, future UTC
  expirations, duplicate expiration preservation, and atomic counter/event writes
  remain unchanged in this release-preparation step.
- Operational logs omit exception/request payloads; debug is off and production
  configuration is validated. Proxy/WSGI/system logs need operator configuration.
- Remaining risks: public-link enumeration/abuse, unimplemented rate limits and
  retention automation, heuristic classification, SQLite contention, synchronous
  report generation, legacy malformed expiry records, and the eligibility/write
  race. See DEPLOYMENT.md before public service launch.

No confirmed application release-blocking defect required a behavior/schema
change in this step. Cleanup was limited to an unused test import. No new tests
were added solely for documentation or ignore-file edits.

## Portfolio presentation

**Recommended description:** Telegram URL shortener with expiring links, private
user management, administrator analytics, and secure CSV exports, built with
Python, Flask, and SQLite.

**Suggested topics:** `python`, `telegram-bot`, `python-telegram-bot`, `flask`,
`sqlite`, `url-shortener`, `analytics`, `csv-export`, `portfolio-project`.

**Release target:** `v1.0.0`. An annotated Git tag is authorized if absent. No
GitHub Release page or production deployment is implied by creating a Git tag.

**Draft release notes:**

- Initial independent portfolio release of PoriaLinkBot.
- URL shortening, duplicate reuse, optional UTC expiration, and private owned links.
- Atomic click events with destination UTM, daily/client, and heuristic bot analytics.
- Private administrator dashboard and bounded, formula-safe CSV exports.
- Offline integration/security tests, safe configuration, and operator documentation.
- No hosted service, performance claim, GeoIP, or unique-visitor feature is included.

Use actual screenshots only; see `screenshots/README.md` for filenames and privacy
review. Do not describe the project as open source until an appropriate license
is deliberately chosen. Confirm source ownership, permission to publish, and
third-party obligations before licensing; no license has been assumed or added.

## Authorized release procedure

The owner accepted the history email visibility and authorized portfolio
publication. Inspect ignored-file boundaries and the disclosed advisory/clean-
install warnings. Do not rewrite history, rotate credentials, change visibility
or ownership, add a license, or force-push during this release.

From the repository root, verify that the existing branch is still `main` and the
existing remote is the intended destination. Inspect remote configuration locally
without copying credential-bearing URLs into logs or support messages. These
commands preserve the existing remote/branch and avoid `git add .` / force-add:

```bash
git status --short
git branch --show-current
git diff --check
git add -- .gitignore .env.example README.md DEPLOYMENT.md RELEASE_CHECKLIST.md requirements.txt requirements.lock ./*.py tests screenshots/README.md
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
git diff --cached
```

Review every staged file, including ignored-artifact checks. Stop if any secret,
database, CSV, log, private image, or unexpected artifact is present. Run the full
suite from README and the dedicated secret/dependency checks before approving.
Only after final validation and staged-diff review pass:

```bash
git commit -m "Prepare PoriaLinkBot v1.0.0 portfolio release"
git push origin main
git tag -a v1.0.0 -m "PoriaLinkBot v1.0.0 portfolio release"
git push origin refs/tags/v1.0.0
```

Check local and remote tags before creating the annotated tag. If it already
exists, inspect it without replacing/deleting it. Verify remote main and the
peeled annotated tag point to the release commit before claiming success.
