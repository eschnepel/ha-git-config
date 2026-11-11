# SSH deploy key with pinned GitHub host keys

Authentication uses a generated ed25519 SSH deploy key (write access) rather than SSH certificates (Enterprise Cloud only), a GitHub App, or a PAT over HTTPS. GitHub's published host keys ship with the integration and are verified strictly, with no trust-on-first-use, which restricts remotes to `github.com`. Git and SSH run via `dulwich` with its paramiko vendor because the Core container has no `git` or `ssh` binary.
