# Contributing

Small, focused pull requests are welcome.

Before opening a pull request:

1. Read the relevant module and its tests.
2. Keep runtime data, credentials, inventories, logs and generated artifacts out of the diff.
3. Run the Python and JavaScript test suites.
4. Run <code>python scripts/source_bundle.py --check</code>.
5. Describe user-visible behavior and any security boundary that changed.

The public repository is intentionally source-only. Do not add a real <code>servers.yaml</code>, SSH key, exported inventory or production screenshot.
