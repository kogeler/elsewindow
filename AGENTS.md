# Elsewindow Agent Guide

Elsewindow is an independently buildable and releasable Linux Python project.
Every maintained input, test, workflow, and artifact owner lives in this
repository. Resolve every input beneath this root and never import another
application's source tree.

## Project map

- `README.md` is the public overview and documentation index. `mkdocs.yml`,
  `doc/`, `doc/site/`, and `tools/audit_docs_site.py` own the rendered site and
  its offline publication audit. `doc/cli.md` is the complete CLI option
  reference; keep its inventory, defaults, and allowed values aligned with
  the production parser.
- `elsewindow/` is the typed installed package. `live-cli.yml` and
  `profiles.yml` are package data and the sole mirrored Xpra profile authority;
  `session.py` owns explicit project security and GUI defaults and applies the
  reviewed clipboard policy from the mirrored configuration to both peers.
  `session_bus.py` owns the private foreground remote notification bus.
  `desktop.py` owns optional desktop prerequisites and foreground portal helpers.
- `bin/elsewindow` is the repository launcher; `python -m elsewindow` and the
  installed console command are the other supported entry routes.
- `elsewindow/machine.py` selects the shared machine/user namespace for all
  Make and repository-launcher venvs beneath `.venvs/`, and for default installed
  Xpra environments. Keep its bootstrap standard-library-only, fail closed on
  invalid OS identity, and never reuse or remove another machine's environments.
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
- `.github/workflows/` contains active root-native CI, documentation, PR
  metadata, dependency submission, and release automation.
- `.github/scripts/` owns standard-library dependency, changelog-to-PR,
  version, and release policy. `doc/maintenance/` owns maintainer procedures.
- `pyproject.toml` owns package metadata, typing, security scanning, and
  coverage policy. Published runtime metadata reads `requirements.in`
  dynamically and must not duplicate its versions.
- `.version` is the only human-maintained version. Dynamic package metadata,
  `elsewindow.__version__`, the changelog section, `vX.Y.Z` tag, release name,
  and notes must resolve from it.
- Ordinary pull requests keep the published `.version` and accumulate
  release-worthy entries under `CHANGELOG.md` `## Unreleased`. Only deliberate
  release preparation advances the version and creates its dated section.
- `requirements.in` and the five `requirements-*.in` files own exact direct
  dependency versions and are the native Dependabot inputs. Their matching
  `requirements*.txt` files are generated hash locks. Never hand-edit a lock.
- `requirements.in` is the only maintained SSH runtime version authority.
  `tools/runtime_dependency.py` reads its current pin for environment and
  artifact/live checks; do not copy that version into code, tests, or documentation.
- `elsewindow/requirements-xpra.in` and its generated hash lock own the separate
  local Xpra environment's matched PyOpenGL additions. Ship this lock in every
  artifact; never install these dependencies into the Elsewindow runtime.
- `elsewindow/requirements-xpra-build.in` and its generated hash lock own only
  temporary accelerator build tools. A missing binary wheel may use the
  hash-verified accelerator source archive without unpinned build isolation;
  remove the builder packages before activating the runtime environment.

## Required runtime contracts

- Perform one deliberate OpenSSH authentication and use only channels backed
  by the owned master. Never reconnect after master loss.
- Open no forwarding or Xpra TCP listener. Disable automatic reconnection and
  every auxiliary Xpra data and device feature except notifications and the
  reviewed clipboard policy. Keep ordinary cursor, wheel, keyboard-state,
  modal-window, DPI and initially unscaled client-scaling behavior enabled.
  Apply `off`, `to-server`, or `both` explicitly to both peers and
  default to `both`.
- Forward application notifications by default using a private foreground
  D-Bus daemon inside each owned remote process group or persistent cgroup.
  Never adopt a desktop/user bus or enable Xpra D-Bus control or bus autolaunch.
  Load the client's tray helper required by the current notification presenter,
  but keep server-side application tray forwarding and Xpra's own tray disabled.
- Run only the reviewed FileChooser and Notification desktop portal interfaces
  on that private bus, with owned foreground helpers and a transient permission
  store. Disable service activation and unrelated portal methods, including
  OpenURI; never adopt a host document portal or mount a document filesystem.
- Missing optional prerequisites produce host-specific package/setup warnings
  and disable only the affected feature for this session; startup never installs
  packages. Journald and log observers must not own application lifetime.
  Essential SSH, ownership, interpreter and Xpra compatibility failures remain
  fatal. Missing persistence prerequisites may select ordinary lifetime only
  without recorded persistent state, with an explicit disconnect-cleanup warning.
- Ordinary sessions run the remote server and application in one
  heartbeat-supervised process group. Explicit `--persistent` sessions run in
  an owned transient user systemd service and survive client and SSH loss.
  Check linger on each invocation and offer to enable it only with interactive
  consent. Cleanup targets only recorded owned resources.
- Aggregate explicitly labeled local/remote Elsewindow and Xpra records in
  the local journal and terminal. Forward new remote records through owned
  SSH mux channels; the remote journal contains only remote sources. Apply one
  shared session ID to every message and native record on both hosts. Keep
  ordinary IDs unique and persistent IDs stable across reconnects and scoped
  to the remote machine, account, and application; isolate concurrent observers.
  Apply one `--log-level` to both peers, default to `warning`, with identical terminal
  filtering. Log observers never reconnect or own application lifetime, and
  their queues and frames are bounded. Clipboard debugging uses
  `--log-level=debug-clipboard`. Journald failures must
  not shorten an already running persistent application's lifetime.
- Before changing an Xpra argument, compare `elsewindow/live-cli.yml` and
  `elsewindow/profiles.yml` byte-for-byte with the current canonical files in
  [`kogeler/xpra:develop/fork-maintenance/`](https://github.com/kogeler/xpra/tree/develop/fork-maintenance/).
- Do not duplicate YAML option or numeric values in Python or tests. Route all
  applicable Xpra version, information, and detach commands through the shared
  YAML command assembler.
- Keep package installation separate from startup. Launchers never modify
  system packages and replace inherited `PYTHONPATH` so owned source and the
  prepared PyPI dependency cannot be shadowed.
- `make runtime-venv` prepares both the isolated Elsewindow environment and a
  separate system-Python Xpra venv. Installed and standalone commands expose
  explicit `--prepare-xpra` setup. Startup only revalidates the current lock,
  interpreter and installed bytes; all local Xpra commands use its owned
  launcher. Never leak PyInstaller's private libraries into system tools.
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
  local DEBs with `libva-drm2`, `python3-opengl`, `python3-venv`, `dbus-daemon`,
  `python3-dbus`, `python3-gi`, `xdg-desktop-portal`, and
  `xdg-desktop-portal-gtk` through APT. Never use
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
  Its same systemd-backed target covers ordinary cleanup and persistent
  linger consent, disconnect/resume identity, and application-exit cleanup.
  An operator-shaped persistent case resumes a lightweight probe of Zed's
  agent-notification windows under a real local window manager.
- Live tests are autonomous Podman runs that work on any host with rootless
  Podman. Never depend on software installed on the operator's machine. To
  cover a third-party application, reproduce its relevant public protocol flow
  as a lightweight probe derived from the pinned upstream source, and name
  that version and those code paths in the probe, instead of shipping the
  application into an image.
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
real test immediately after each atomic change, through
`make test-focused TESTS=...` or the narrowest owning Make target.

Run every build, image, container, and test action only through a Make target.
Never run ad-hoc build, Podman, container, or test commands, one-off drivers,
or throwaway derived images, including for diagnosis or reproduction. When a
build or test procedure is needed more than once, or a reproduction needs a new
scenario, first automate it as a Make target or extend an existing one, and
document it with its owning procedure. Reading sources, logs, and
documentation is not a build or test action.

When `make live-test` fails, fix the cause and resume at the failed case with
`make live-test LIVE_FROM=<case>`; repeat for every later failure until the
matrix reaches its end. Then run one complete `make live-test` without
`LIVE_FROM`; only that complete run validates the change. If it fails, apply
the same resume procedure from the failed case and finish with another
complete run.

The privileged PR metadata workflow executes only trusted default-branch code.
Treat the pull-request head `CHANGELOG.md` as bounded inert data and replace
only the single marker-delimited body section. A populated `## Unreleased`
section must be rendered without requiring a `.version` change. Release
automation must decide from exact external publication state: an existing
complete release for the current version skips reusable release CI and all
publication jobs even as unreleased changes accumulate.

Every local gate reads the current filesystem bytes, including modified and
untracked maintained files. Never select inputs from the Git index, require a
commit, derive normalization from commit metadata, or persist a successful
gate result as authority for a later run. Dependency environments may cache
installed tools only when their complete current lock is revalidated before
use. Dependabot updates only the eight maintained requirements inputs and their
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

After changing a direct dependency in any requirements input, run `make lock` and
review all eight generated locks. Use `make refresh-dependencies` only for an
intentional whole-tree upgrade.

Use `apply_patch` for source edits and preserve unrelated work. Never create a
commit unless the user explicitly requests one in the current conversation.
Never push.
