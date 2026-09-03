# Elsewindow Agent Guide

Elsewindow is an independently buildable and releasable Linux Python project.
Every maintained input, test, workflow, and artifact owner lives in this
repository. Resolve every input beneath this root and never import another
application's source tree.

## Project map

- `README.md` is the public overview and documentation index. `mkdocs.yml`,
  `doc/`, `doc/site/`, and `tools/audit_docs_site.py` own the rendered site and
  its offline publication audit.
- `elsewindow/` is the typed installed package. `live-cli.yml` and
  `profiles.yml` are package data and the sole Xpra option authority.
- `bin/elsewindow` is the repository launcher; `python -m elsewindow` and the
  installed console command are the other supported entry routes.
- `tools/install_xpra_release.py` is the standalone verified Xpra package
  installer for Debian 13 and Ubuntu 26.04.
- `tools/prepare_xpra_images.py` prepares release-backed client and target
  images by immutable ID.
- `tools/container_payload.py` owns every project container file transfer.
- `tools/project_tree.py` enumerates maintained current-tree files without Git
  metadata and is the sole container-context inventory authority.
- `containers/` contains only Elsewindow's client, target, governance, and
  installer images.
- `tests/` contains unit, policy, package, installer, and the single automatic
  ephemeral-key live topology.
- `.github/workflows/` contains active root-native CI, documentation,
  dependency submission, and release automation.
- `.github/scripts/` owns standard-library dependency, version, and release
  policy. `doc/maintenance/` owns maintainer procedures.
- `pyproject.toml` owns package metadata, typing, security scanning, and
  coverage policy. Published runtime metadata reads `requirements.in`
  dynamically and must not duplicate its versions.
- `.version` is the only human-maintained version. Dynamic package metadata,
  `elsewindow.__version__`, the changelog section, `vX.Y.Z` tag, release name,
  and notes must resolve from it.
- `requirements.in` and the five `requirements-*.in` files own exact direct
  dependency versions and are the native Dependabot inputs. Their matching
  `requirements*.txt` files are generated hash locks. Never hand-edit a lock.

## Required runtime contracts

- Perform one deliberate OpenSSH authentication and use only channels backed
  by the owned master. Never reconnect after master loss.
- Open no forwarding or Xpra TCP listener. Disable automatic reconnection and
  auxiliary Xpra data and device features.
- Start the remote server and application in one heartbeat-supervised process
  group. Cleanup targets only recorded owned resources.
- Before changing an Xpra argument, compare `elsewindow/live-cli.yml` and
  `elsewindow/profiles.yml` byte-for-byte with the current canonical files in
  [`kogeler/xpra:develop/fork-maintenance/`](https://github.com/kogeler/xpra/tree/develop/fork-maintenance/).
- Do not duplicate YAML option or numeric values in Python or tests. Route all
  applicable Xpra version, information, and detach commands through the shared
  YAML command assembler.
- Keep package installation separate from startup. Launchers never modify
  system packages and replace inherited `PYTHONPATH` so owned source and the
  prepared PyPI dependency cannot be shadowed.
- Import `ssh_wrapper` only from the prepared environment. Do not vendor it,
  add another import path, or build a replacement wheel here.
- Treat the maintained Xpra fork release and its live matrices as the authority
  for codec, rendering, application, and downstream-patch correctness. This
  project tests public commands, package provenance, profile assembly, and its
  SSH-owned lifecycle without internal Xpra probes or patch identifiers.

## Installer and container contracts

- The Xpra installer rejects unsupported operating systems, releases, and
  architectures before network access. Existing Xpra inventory requires an
  exact interactive `y` or `yes`, read from `/dev/tty` when available.
- Non-root installation invokes `/usr/bin/sudo`, copies validated inputs into
  a root-owned mode-`0700` stage, revalidates immutable copies and inventory,
  and purges only the confirmed set immediately before the APT transaction.
- Package selection is the symmetric consumed dependency closure. Validate the
  native libva encoder and decoder, libyuv, GTK OpenGL, common/server assets,
  X11 bindings, and Ubuntu Wayland modules in their owning DEBs. Install exact
  local DEBs with `libva-drm2` and `python3-opengl` through APT. Never use
  `dpkg -i`, `autoremove`, or an added Xpra APT source.
- Every project-owned Podman transfer uses the bounded payload pipe. Do not add
  bind mounts, named data volumes, or `podman cp`.
- Every rootless runtime container that consumes subordinate IDs declares a
  bounded `--userns=auto:size=...` range. Never use unbounded `auto`, `keep-id`,
  `nomap`, or `--userns=host`.
- The disposable Ubuntu SSH target assigns the `sshd` privilege-separation
  account to its dedicated low-ID `_ssh` group so its 2,048-ID namespace is
  sufficient; do not restore the legacy GID 65534 or widen the namespace.
- The live harness has one automatic route using both immutable release-backed
  images, the production profile assembler, and a newly generated Ed25519 key.
- Installer acceptance and image preparation resolve the newest valid fork
  package release during each run. Never require a predecessor or retained
  release history as a gate input.

## Change and validation workflow

Keep repository-owned text in English and internal documentation links
relative. Never publish operator-specific absolute paths, usernames, home or
runtime directories, or sibling-checkout layouts in repository documentation
or operator-facing reports. Refer to an external source repository by its
canonical HTTPS GitHub URL; a locally available checkout is only an inspection
detail and its host path must not be recorded. Refer to downstream Xpra work
only as part of the maintained fork at
[`kogeler/xpra`](https://github.com/kogeler/xpra/tree/develop); do not revive a
retired standalone repository identity.
Update implementation, focused tests, and the owning user, architecture,
security, development, or maintenance documentation together. Run the smallest
real test immediately after each atomic change.

Every local gate reads the current filesystem bytes, including modified and
untracked maintained files. Never select inputs from the Git index, require a
commit, derive normalization from commit metadata, or persist a successful
gate result as authority for a later run. Dependency environments may cache
installed tools only when their complete current lock is revalidated before
use. Dependabot updates only the six `requirements*.in` inputs and their
matching pip-compile locks; keep `pyproject.toml` excluded from its pip
manifests and both local resolver stages aligned with Dependabot's resolver.
When the operator supplies a local reference checkout, inspect that checkout
directly and do not substitute an internet copy.

```bash
make format
make check
make docs-audit
make package
make standalone
make smoke
make live-preflight
make live-test
```

`make ci` adds online lock re-resolution, audit, and CPython 3.13
compatibility. Run
`make runtime-venv` before the repository launcher. Run destructive installer
acceptance only through `make xpra-installer-test`; it uses disposable
containers and never changes host package inventory.

After changing a direct dependency in `requirements*.in`, run `make lock` and
review all six generated locks. Use `make refresh-dependencies` only for an
intentional whole-tree upgrade.

Use `apply_patch` for source edits and preserve unrelated work. Never create a
commit unless the user explicitly requests one in the current conversation.
Never push.
