# Security Policy

> See also: [Security on the docs site](https://docs.fim.ai/security) for a quick overview with self-hosted best practices.

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Previous minor | Security fixes only |
| Older | No |

We recommend always running the latest version. Security patches are backported to the previous minor release on a best-effort basis.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

### For sensitive vulnerabilities

Use [GitHub Security Advisories](https://github.com/fim-ai/fim-one/security/advisories/new) to report vulnerabilities privately. This ensures the report is only visible to maintainers until a fix is released.

Alternatively, email **security@fim.ai** with:

- A description of the vulnerability
- Steps to reproduce (or a proof-of-concept)
- Affected versions
- Potential impact assessment

### For non-sensitive issues

For low-severity issues (e.g., missing security headers, informational disclosures that don't expose sensitive data), you may open a regular GitHub issue with the `security` label.

## Response Timeline

| Stage | Target |
|-------|--------|
| **Acknowledgment** | Within 48 hours (business days) |
| **Initial assessment** | Within 5 business days |
| **Fix development** | Depends on severity (critical: ASAP, high: 2 weeks, medium/low: next release) |
| **Public disclosure** | After fix is released and users have had time to update |

## Scope

The following are in scope for security reports:

- Authentication and authorization bypass
- SQL injection, command injection, or code execution
- Cross-site scripting (XSS) or cross-site request forgery (CSRF)
- Credential or API key exposure
- Privilege escalation between users or organizations
- Data leakage across tenant boundaries

The following are **out of scope**:

- Vulnerabilities in third-party dependencies (report upstream; we monitor via Dependabot)
- Social engineering attacks
- Denial of service (DoS) without a realistic attack vector
- Issues in the demo/cloud environment that don't affect self-hosted deployments

## Security Best Practices for Self-Hosted Deployments

- Keep your `.env` file out of version control (it's in `.gitignore` by default)
- Use strong, unique values for `JWT_SECRET_KEY`
- Run behind a reverse proxy (nginx, Caddy) with TLS in production
- Use PostgreSQL (not SQLite) for multi-user production deployments
- Regularly update to the latest release

## Recognition

We appreciate responsible disclosure. Contributors who report valid security vulnerabilities will be credited in the release notes (unless they prefer to remain anonymous).

### Acknowledgements

| Reporter | Advisory | Fixed in |
|----------|----------|----------|
| [kta1kri](https://github.com/kta1kri) | [GHSA-fr6j-vx23-x4c7](https://github.com/fim-ai/fim-one/security/advisories/GHSA-fr6j-vx23-x4c7): stored database connector password sent to an attacker-chosen host via test-connection | 0.9.1 |
