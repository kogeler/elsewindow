# Development

The consumer metadata pins the published `ssh-wrapper==0.1.0` distribution.
Runtime and development environments install its PyPI wheel through the
project-owned hash locks. Unit tests import that installed distribution;
pytest never adds an alternate source directory to its import path.

Prepare and run the complete local suite:

```bash
make dev-venv
make format
make check
```

All Make environments live beneath `.venvs/<machine-user-key>/`; the standard-
library-only `elsewindow/machine.py` selects the same namespace for Make and the
repository launcher before any venv exists. It uses an application-specific
hash of `/etc/machine-id` and the local UID, independently of the checkout path,
hostname, or boot. Shared checkouts cannot reuse another machine's Python or
native extensions. A missing or invalid OS identity fails explicitly instead
of selecting a common directory. Cloned systems must have distinct OS machine
IDs. Old root-level venvs are neither reused nor migrated or deleted.

The five maintainer environments are separated by responsibility, while
`venv-runtime` contains only production requirements:

- `venv-quality` for Ruff, mypy, Bandit, audit, and licenses;
- `venv-test` for pytest and coverage;
- `venv-package` for deterministic wheel/sdist construction;
- `venv-standalone` for native PyInstaller builds;
- `venv-docs` for strict MkDocs rendering.

`make runtime-venv` also depends on `make xpra-venv`. This separate environment
uses the system Xpra Python with system site-packages; only its hash-locked
PyOpenGL pair overrides distribution Python modules. The runtime-only bootstrap
target `make runtime-python-venv` is used by the container harness, which does
not need Xpra installed on its orchestration host. Every session validates the
current Xpra lock, interpreter identity, versions, installed file hashes and
launcher. Explicit setup alone may repair an owned stale environment.

`make clean` removes environments only from the current machine/user namespace;
it still removes shared build artifacts and caches as before. The complete
`.venvs/` tree is excluded from Git, source distributions, and container payloads.
The shared wheel/sdist/standalone smoke prepares the default machine-scoped XDG
location before testing an exact explicit override and stale-environment repair.
The CPython 3.13 compatibility image includes Make and the distribution's
`/usr/bin/python3` to exercise the real pre-venv launcher bootstrap as well.

`make runtime-venv` and `make dev-venv` revalidate the installed wrapper and
its distribution version, not just the lock marker. If a Python transition makes an existing
environment unable to import its packages, the owning target rebuilds that
environment from the hash lock.

The unit suite covers public configuration, strict profile parsing, complete
YAML-to-argv assembly, lifecycle failures and cleanup, public Xpra command
integration, release validation, image provenance, and the installer harness.
Journal tests use private Unix datagram receivers, including the isolated
remote source bootstrap, and never send unit-test records to the host journal.
The [CLI reference](cli.md) is the single option reference. Tests compare its
complete option inventory, defaults, and choices with the production parser;
the profile tables are checked against the packaged YAML mirrors.

Run the destructive package acceptance only on a host with rootless Podman:

```bash
make xpra-installer-test
```

It builds fresh Debian 13 and Ubuntu 26.04 containers and selects the newest
valid package release currently published by the maintained fork. Each guest
validates a clean installation, a declined replacement, and a confirmed
purge/reinstallation of that same current release. The gate never requires a
retained predecessor and never installs host packages.

### Standalone system installer

`tools/install_xpra_release.py` uses only the Python standard library and
system APT/dpkg commands. It supports only amd64 Debian 13 and Ubuntu 26.04,
checked before network access. With no arguments it installs; `resolve` is the
read-only release diagnostic.

When an Xpra inventory exists, the helper prints every package, version, and
dpkg status and accepts only interactive `y` or `yes` from `/dev/tty`. It then
validates release metadata and ancestry, asset size and SHA-256, manifest,
checksum list, archive structure, exact versions and architectures, and actual
DEB members. A non-root process invokes `/usr/bin/sudo` itself, copies the
validated payload into a root-owned mode-`0700` stage, revalidates it, simulates
the local-DEB APT transaction, rechecks the inventory, purges only that set,
and installs through APT.

Capability validation requires the native libva encoder and decoder, libyuv,
GTK OpenGL, common/server assets, X11 bindings, and Ubuntu Wayland modules in
their owning packages. APT explicitly requests `libva-drm2`,
`python3-opengl`, `python3-venv`, `dbus-daemon`, and `python3-dbus`;
post-install verification imports every
required module. Any
missing, duplicate, symlinked, or unsafe payload fails before mutation.

Resolve and prepare the latest release-backed client and target images with:

```bash
make xpra-release-status
make xpra-images
```

The project owns one automatic live topology. Preflight confirms that the two
immutable images and rootless Podman are available; the full target streams
the current project into a confined client image that already contains the
hash-locked published wrapper, without exposing host paths:

```bash
make live-preflight
make live-test
```

The full test builds and verifies the wheel, excludes package source from its
payload, clean-installs the wheel as the disposable client user, and proves
that installed import before the real cases. It prepares the Xpra venv through
the installed command, using hash-verified wheels cached in the immutable client
image and no access to PyPI from the runtime network. The public OpenGL command
must report the matching accelerator and `zerocopy=True`. It uses the same production
profile assembler as the launcher and proves a remote Wayland application
window, picture updates, one authentication per invocation, normal detach,
abrupt SSH-master loss, selective remote cleanup, and complete removal of
labelled Podman resources. The same target boots real systemd/logind, without
privileged mode or a wider ID namespace. The matrix first refuses and then
approves linger through a real terminal and the production mux. Persistent
cases reuse the same window, process-identity and profile checks across detach,
client kill, deliberate master closure, master kill and cancellation. They
wait beyond the ordinary heartbeat lease with all connections closed, compare
the same application/server PID and service token after each reconnect, and
check cleanup after application exit with both zero and nonzero status.
The same public GTK fixture exercises text/modifier input, both wheel axes,
custom cursor pixels through XFixes, modal-dialog opening/keyboard dismissal, and
notification delivery to a real freedesktop service on the disposable client's
private bus. No Xpra implementation objects or test-only input options are
used. The private remote notification bus retains its process identity across
persistent reconnects and is gone after the owned application/session ends.
The client permits modal hints, but the fixture does not equate GTK's Wayland
modal grab with X11 window-manager properties; compositor metadata correctness
remains in the maintained fork.
Both guests run real journald. The confined client adds only a bounded private
runtime tmpfs; its journal daemon has no capabilities, and the product remains
non-root. A container-only bounded journal policy retains the complete matrix
for exact comparisons, without changing any host's journal policy.
The existing cases exercise the warning default, info lifecycle
records and clipboard debugging on both hosts. They compare local journal
messages with stdout/stderr, including severity routing, and verify native
record identity and filtering throughout ordinary and persistent lifetimes.
The client must contain all four source labels, while the target contains
only remote sources; forwarded records must match originals including source
PID. The persistent case also loses a log-only SSH channel and proves the
application identity survives. Unit tests exercise bounded IPC backpressure,
frame rejection, subscription cleanup and resubscription without history.
Every journal message must visibly contain its native session ID, shared by
both peers and stable across persistent reconnects. Real isolated subprocess
tests complement a simultaneous second ordinary session in the existing live
detach case: each authenticates once, has its own ID and journal records, and
closing one leaves the other application alive. The isolated subprocess
tests cover simultaneous subscribers for the same and different sessions,
including one observer leaving without affecting the others; identity tests
separate remote machines and accounts and cover deferred startup failures.
The graphical fixture uses at most two available CPUs before loading GTK,
bounding native worker pools on high-core-count hosts
without relaxing container process limits or changing production Xpra profiles.
Unrelated resources must survive and every invocation still authenticates once.
The disposable account has narrowly scoped sudo permission only for its own
linger activation. Codec and GPU correctness remain the maintained
fork's responsibility.

Do not add internal Xpra source probes, downstream patch identifiers, or
patch-specific regressions to this project. If the newest release fails inside
Xpra, preserve its immutable release identity and the externally observed
command/error, then hand those facts to the operator for investigation in the
fork. The fork's own live matrices decide whether its current code and active
queue satisfy the required Xpra behavior.

All runtime containers declare bounded automatic user namespaces. The live
client and target use 2,048 subordinate IDs, enough for the required UID 1001,
without reserving the host's complete rootless mapping. The target image binds
OpenSSH's privilege-separation account to its dedicated low-ID group so its
legacy `nogroup` assignment does not force a 65,536-ID range.

### Distribution Artifacts

Build, normalize, verify, and smoke the Python artifacts with:

```bash
make package
make reproducibility
make smoke-wheel
make smoke-sdist
```

Build and smoke the current native Linux architecture with:

```bash
make standalone
make smoke-standalone
```

CI performs the standalone commands independently on native amd64 and arm64
runners. `make checksums` deliberately fails until all four release artifacts
are assembled in `dist/`; it then writes the one exact release inventory.
Wheel, sdist and standalone smoke share the same environment-setup acceptance:
outside the checkout, install real locked PyPI wheels without an index, execute
an argv fixture through the prepared interpreter, reject changed installed
bytes, then repair them explicitly using the hash-verified source archive and
locked temporary builder without network access. A compiler and system Python
development headers are required for these smoke tests, even on amd64.
Frozen smoke also exercises independent
invocations so an environment cannot depend on a previous extraction directory.

Render and audit the documentation site with:

```bash
make docs-audit
```

This rebuilds `site/` from the current files under `doc/`, then checks local
routes, links, anchors, image alternatives, canonical URLs, both sitemap
representations, `robots.txt`, and `llms.txt` without network access. Use
`make docs-serve` for a local preview; generated `site/` is never a maintained
input.

Before changing production Xpra arguments, compare
[`elsewindow/live-cli.yml`](../elsewindow/live-cli.yml) and
[`elsewindow/profiles.yml`](../elsewindow/profiles.yml) byte-for-byte with
the current `fork-maintenance/` files on the live `kogeler/xpra:develop`
branch. Synchronize drift first. Do not restate canonical option or numeric
values in Python or tests.

Update user and maintainer documentation in the same change as behavior. Run a
real focused test as soon as an atomic implementation step is runnable rather
than accumulating debugging for the end.
