# Security Policy

## Reporting Vulnerabilities

Please report security issues privately to project maintainers. Do not open public issues for active vulnerabilities.

Include:
- affected component(s),
- reproduction details,
- impact assessment,
- suggested remediation (if available).

## Secret Handling

- Never commit secrets, `.env`, cloud keys, or tokens.
- Use environment injection and secret managers for deployment.
- Run secret scanning before release and CI merge.
