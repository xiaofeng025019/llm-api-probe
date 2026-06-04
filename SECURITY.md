# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| latest  | yes       |
| older   | no        |

Only the latest commit on the `main` branch receives security updates. Please
update to the latest version before reporting an issue.

## Reporting a vulnerability

**Please do not file a public GitHub issue for security bugs.**

Report privately by emailing the maintainers (see commit history for contact
info) or by opening a [GitHub Security Advisory][advisory]. A private report
gives us time to release a fix before the issue becomes public.

[advisory]: https://github.com/<owner>/<repo>/security/advisories/new

Include in your report:

- A clear description of the issue and its impact
- Reproduction steps or a proof-of-concept
- Affected versions
- Any relevant logs or screenshots

We will acknowledge your report within 3 business days and follow up with a
plan within 10 business days.

## Security model

This is a **local-first** service intended to run on `127.0.0.1` for personal
use. The following are **by design**:

- **API keys are stored in plaintext** in the local SQLite database. This is
  a deliberate trade-off for the "personal local" use case. If you need to
  expose this service to a network, put it behind a reverse proxy with TLS +
  BasicAuth, and consider [issue #N](#) for secret-at-rest encryption.
- **No authentication** for any endpoint. The server binds to `127.0.0.1` by
  default.
- **Log redaction**: API keys are truncated to `sk-…xxxx` form before being
  written to logs.

## Threat model assumptions

- Adversary does not have shell access to the host.
- Adversary does not have network access to the host (the default bind is
  loopback).
- Adversary can read the local SQLite file only with local file-system access.

## Out of scope

- Third-party LLM provider security (OpenAI, Anthropic, Google)
- Network exposure beyond `127.0.0.1` (use a reverse proxy)
- Database encryption at rest
- Multi-user access control

## Disclosure policy

We follow a coordinated disclosure model. Please give us a reasonable window
(typically 90 days) to fix the issue before public disclosure.
