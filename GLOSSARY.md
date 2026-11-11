# Git Config

Puts a Home Assistant configuration directory under git control, synchronised with a private GitHub repository from within the HA UI.

## Language

**Config Repo**:
The git repository whose working tree is the whole HA configuration directory, tracking one remote and one branch.
_Avoid_: Backup, project, workspace

**Deploy Key**:
The SSH key pair the integration generates to authenticate against the remote; the public half is registered on GitHub with write access.
_Avoid_: Certificate, credential, token

**Managed Ignore Block**:
The marked section of the configuration directory's `.gitignore` that the integration owns and edits; lines outside it are never touched.
_Avoid_: Ignore list, exclusions

**Protected Entry**:
An ignore entry (`.storage/`, `secrets.yaml`) that is always present in the Managed Ignore Block, re-added whenever it goes missing, so that secrets never leave the system.

**Pending Changes**:
Files in the configuration directory that differ from the last commit and are not ignored.
_Avoid_: Dirty state, diff

## Actions

**Commit & Push**:
Stage all Pending Changes, commit them, and push the branch to the remote. Does nothing when there are none.
_Avoid_: Save, backup, sync up

**Fetch**:
Download remote commits without changing the working tree, so that behind/ahead counts are current.
_Avoid_: Check for updates

**Fetch & Pull**:
Fetch, then fast-forward the local branch to the remote and apply the result to the running system.
_Avoid_: Update, sync down

**Apply**:
After a pull, bring the running system in line with the changed files: reload the configuration domains that can be reloaded, otherwise require a restart. A file's domain is the top-level `configuration.yaml` key that includes it.

**Restart Required**:
A Repairs issue raised when pulled files cannot be hot-reloaded, offering a restart as the fix.
