# Security policy

Overseer Network is designed for a private, single-user deployment. It is not a multi-tenant service and is not intended to be exposed directly to the public internet.

## Reporting a vulnerability

Please do not open a public issue for an undisclosed credential, authentication bypass, command injection, or remote-operation flaw. Contact the maintainer privately with:

- affected commit or release;
- reproducible steps or a minimal proof of concept;
- impact and suggested mitigation;
- whether any credentials or infrastructure data may have been exposed.

Never attach private keys, live inventories, SSH fingerprints, access tokens, logs with command lines, or registry exports. Rotate exposed credentials before sending a report.

## Operational boundaries

- Keep <code>OVERSEER_HOME</code> outside the public repository for real deployments.
- Use a dedicated least-privilege SSH account where possible.
- Verify SSH host-key fingerprints independently before trusting a node.
- Treat the terminal as fully privileged for its configured SSH account.
- Review the operation plan and confirmation token before allowing a restart.
- Keep backups of the private registry and secrets outside the checkout.
