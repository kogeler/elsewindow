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

The five maintainer environments are separated by responsibility, while
`venv-runtime` contains only production requirements:

- `venv-quality` for Ruff, mypy, Bandit, audit, and licenses;
- `venv-test` for pytest and coverage;
- `venv-package` for deterministic wheel/sdist construction;
- `venv-standalone` for native PyInstaller builds;
- `venv-docs` for strict MkDocs rendering.

`make runtime-venv` and `make dev-venv` revalidate the installed wrapper and
its distribution version, not just the lock marker. If a Python transition makes an existing
environment unable to import its packages, the owning target rebuilds that
environment from the hash lock.

The unit suite covers public configuration, strict profile parsing, complete
YAML-to-argv assembly, lifecycle failures and cleanup, public Xpra command
integration, release validation, image provenance, and the installer harness.

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
their owning packages. APT explicitly requests `libva-drm2` and
`python3-opengl`; post-install verification imports every required module. Any
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
that installed import before the real cases. It uses the same production
profile assembler as the launcher and proves a remote Wayland application
window, picture updates, one authentication per invocation, normal detach,
abrupt SSH-master loss, selective remote cleanup, and complete removal of
labelled Podman resources. Codec and GPU correctness remain the maintained
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
