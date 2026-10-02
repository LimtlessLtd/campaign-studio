# Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** for a private report. Do not post credentials or private
campaign files in an issue. Include the affected version, reproduction with synthetic data and impact.

Campaign Studio is intended for a trusted user's own computer. It binds to `127.0.0.1`; there is no login,
TLS termination or multi-user authorization. Do not expose it through port forwarding or a public proxy.
Host checks and write headers help isolate it from other web origins. Local software with filesystem access
can still read campaign files. Exported GM journals contain secrets.

Structured map workflows disable model tools. The optional legacy general Claude request runner has file
editing tools and is not a sandbox; use it only with trusted requests. Campaign data sent to a configured
AI or image provider follows that provider's policies. API keys stay in environment variables.

The initial release is alpha software. Security fixes target the latest release and main branch.
