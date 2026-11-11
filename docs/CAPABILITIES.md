# Git Config: Capabilities

What the `git_config` integration does for a Home Assistant user. Terms are defined in [GLOSSARY.md](../GLOSSARY.md); rationale is in [docs/adr](./adr).

## Scope

- Home Assistant OS on a Raspberry Pi 5 (aarch64). Container and Core installs are deferred.
- One Config Repo (the whole configuration directory), one GitHub remote, one branch. One config entry.
- GitHub remotes over SSH only. Other hosts are out of scope.

## Setup (config flow)

1. Enter the SSH remote URL (`git@github.com:owner/repo.git`), the branch, and the commit author name and email (default `Home Assistant <homeassistant@localhost>`).
1. The integration generates a Deploy Key and shows its public half. The user registers it on GitHub with write access, then confirms.
1. The integration verifies access, then adopts the configuration directory:

| Starting state | Result |
| -- | -- |
| No `.git`, remote empty | `git init`, initial commit, push |
| No `.git`, remote has history | Fetch, then mixed reset to the remote branch. Local files are never overwritten; differences appear as Pending Changes |
| `.git` exists, no remote | Add remote. Remote empty: push local history. Shared ancestry: normal behind/ahead rules. Unrelated history: refuse with a Repairs issue |
| `.git` exists, `origin` or branch differs from the input | Flow stops with an explicit error. Nothing is rewritten or switched |

4. The Managed Ignore Block is seeded with `secrets.yaml`, `.storage/`, `*.db*`, `*.log*`, `.cloud/`, `deps/`, `tts/` and `__pycache__/`.

## Ignore management (options flow)

- Add and remove entries in the Managed Ignore Block of `.gitignore`. Lines outside the block are never read or modified.
- Protected Entries (`.storage/`, `secrets.yaml`) are not offered for removal and are re-added if missing, including after edits made outside Home Assistant. This is enforced at startup and before every Commit & Push.
- Change the commit author name and email.
- Regenerate the Deploy Key and show the new public key.

## Actions

| Action | Button | Service | Behaviour |
| -- | -- | -- | -- |
| Commit & Push | Commit & Push | `git_config.commit_push` (optional `message`) | Stage all Pending Changes, commit, push. Already-committed local commits are pushed too. No-op when there is nothing to commit or push. Refused when the remote has new commits. Default message is `HA config update <timestamp>` plus a changed-file summary |
| Fetch | Fetch | `git_config.fetch` | Download remote commits only. Working tree unchanged |
| Fetch & Pull | Fetch & Pull | `git_config.pull` | Fetch, fast-forward, then Apply |

An automatic Fetch runs at startup and every hour afterwards. The interval is fixed.

### Pull rules

A pull is refused, leaving the working tree untouched and raising a Repairs issue, when:

- There are Pending Changes.
- The local and remote branches have diverged, or have unrelated history.
- The incoming commits add or change a Protected Entry.

It never merges, resets or force-updates. Resolution happens outside Home Assistant.

### Apply

Runs after a successful pull, once Home Assistant's config check passes. If the check fails, nothing is reloaded or restarted, and a Repairs issue shows the error.

- Each changed file is resolved to a domain by following the includes in `configuration.yaml` (`!include`, `!include_dir_list`, `!include_dir_merge_list`, `!include_dir_named`, `!include_dir_merge_named`) and packages.
- A changed `configuration.yaml` is diffed per top-level key, old against new.
- A domain is reloadable if it registers a `reload` service, found at runtime. The integration calls it. The `homeassistant:` key maps to `homeassistant.reload_core_config`.
- **Restart Required** is raised when a changed file or key cannot be resolved to a reloadable domain, or when anything under `custom_components/` changes. The Repairs fix restarts Home Assistant.
- File types that carry no configuration (docs, images, `www/`) cause no action.

## Entities

| Entity | Type | Meaning |
| -- | -- | -- |
| Commit & Push, Fetch, Fetch & Pull | button | Actions above |
| Commits behind | sensor | Remote commits not yet local, as of the last Fetch |
| Commits ahead | sensor | Local commits not yet pushed |
| Pending changes | sensor | Count of Pending Changes, checked locally every few minutes |
| Last fetch | sensor (timestamp) | Time of the last successful Fetch |
| Last error | sensor | Most recent failure message, cleared on success |

## Repairs issues

| Issue | Raised when | Fix |
| -- | -- | -- |
| Restart Required | Apply cannot hot-reload the pulled files | Restart Home Assistant |
| Pulled config is invalid | Config check fails after a pull | Shows the error; the user corrects the repo |
| Pull refused | Pending Changes, divergence, unrelated history or Protected Entry in the remote | Explains the cause; resolved outside Home Assistant |
| Secrets in history | Adoption or pull finds a Protected Entry path in history | Advises rotating the secrets. History is not rewritten |
| Authentication failed | GitHub rejects the Deploy Key | Links to the regenerate-key flow |
| Host key mismatch | The remote's SSH host key is not one of GitHub's published keys | Informational; connection is refused |

Issues clear themselves once the cause is gone.

## Security guarantees

- `.storage/` and `secrets.yaml` never leave the system. They are untracked from the index before each commit, and a commit or push is refused if either would still be staged.
- Remote content never overwrites them locally.
- GitHub's host keys are pinned and verified strictly, with no trust on first use.
- The Deploy Key is stored in the config entry, inside `.storage/`, which is itself a Protected Entry.

## Removal

Removing the integration deletes the stored Deploy Key. `.git`, `.gitignore` and the Managed Ignore Block stay in place.

## Out of scope

- Container and Core installs, and non-GitHub remotes.
- Multiple repositories, branch switching, merges, rebases, force operations and history rewriting.
- Automatic commits and scheduled pushes. Commit & Push is always user-triggered.
- SSH certificates (GitHub Enterprise Cloud), GitHub Apps and HTTPS tokens.
- Conflict resolution and diff or history browsing in the UI.
