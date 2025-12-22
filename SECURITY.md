# Security Policy

## Reporting a Vulnerability

We take the security of MCP Hangar seriously. If you believe you have found a security vulnerability, please report it to us as described below.

**Please do not report security vulnerabilities through public GitHub issues.**

### How to Report

1. **Email**: Send details to the project maintainers (contact information in the repository)
2. **Private Disclosure**: Use [GitHub's private vulnerability reporting](https://github.com/mapyr/mcp-hangar/security/advisories/new) if available

### What to Include

Please include the following information in your report:

- Type of vulnerability (e.g., command injection, path traversal, etc.)
- Full paths of source file(s) related to the vulnerability
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the vulnerability and how it could be exploited

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Within 30 days for critical issues

### What to Expect

1. Acknowledgment of your report
2. Assessment of the vulnerability
3. Development and testing of a fix
4. Coordinated disclosure timeline
5. Credit in the security advisory (unless you prefer to remain anonymous)

## Security Features

This project implements multiple security layers. For detailed information, see [docs/SECURITY.md](docs/SECURITY.md).

### Key Security Features

- **Input Validation**: All inputs validated at API boundaries
- **Command Injection Prevention**: Commands and arguments sanitized
- **Rate Limiting**: Token bucket algorithm prevents abuse
- **Secrets Management**: Sensitive data masked in logs
- **Container Security**: Dropped capabilities, read-only filesystem, network isolation

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Security Best Practices

When using this project:

1. **Keep dependencies updated**: Regularly update to the latest version
2. **Use container mode**: For untrusted MCP providers, use container isolation
3. **Limit network access**: Use `network: none` when possible
4. **Review configurations**: Audit provider configurations before deployment
5. **Monitor logs**: Enable audit logging for security events

## Acknowledgments

We appreciate responsible disclosure and will acknowledge security researchers who help improve our security.
