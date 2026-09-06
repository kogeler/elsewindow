# Elsewindow

`elsewindow` starts one GUI application on a remote Linux host and shows
its windows through Xpra. One owned OpenSSH ControlMaster carries every Xpra
control channel; the command opens no forwarding or Xpra TCP listener and
cleans only the server, application process group, sockets, and runtime state
created by that invocation.

The application renders through the remote Wayland compositor and remote GPU.
Only Xpra picture, input, clipboard, and control traffic crosses SSH.

## Install And Run

The Python distribution requires Linux, CPython 3.13 or 3.14, OpenSSH,
`false`, a local graphical session, and compatible Xpra packages and an
accessible system journal on both systems. Install with pip or pipx after the
first release:

```bash
python3.14 -m pip install "elsewindow==0.2.0"
# or: pipx install "elsewindow==0.2.0"
elsewindow --prepare-xpra
elsewindow --ssh-alias agents-a -- xterm
```

Check packaged versions, profile digests, Linux support, and local commands
without opening a connection:

```bash
elsewindow --diagnose
```

The published distribution resolves the exact reviewed
[`ssh-wrapper==0.1.0`](https://pypi.org/project/ssh-wrapper/0.1.0/) dependency.
For a source checkout, prepare the hash-locked runtime and use the repository
launcher from any working directory:

```bash
make runtime-venv
./bin/elsewindow --help
```

This one make target prepares the isolated Elsewindow runtime and a separate
system-Python venv for local Xpra. The latter keeps GTK and native Xpra modules
in their system packages while installing a hash-locked matching PyOpenGL and
accelerator pair. Session startup never invokes pip or changes system packages.
Both environments are selected automatically beneath `.venvs/<machine-user-key>/`,
so each machine prepares its own Python and native additions in a shared checkout.
See [checkout setup](doc/getting-started.md#prepare-a-source-checkout) for migration
and system-command behavior.

Use a direct authority when an OpenSSH alias is not appropriate:

```bash
elsewindow \
  --host host.example \
  --user desktop-user \
  --port 2222 \
  -- /opt/application/bin/application
```

See the [CLI reference](doc/cli.md) for every option, default, and allowed
value, including encoding/network profiles, clipboard policy, logging, and
persistence. Reviewed profile arguments come from the packaged YAML mirrors.
The [Xpra guide](doc/xpra.md) covers hardware, application, and lifecycle
behavior; the [security model](doc/security.md) explains clipboard and logging
trust boundaries.

## Installing The Maintained Xpra Build

[`tools/install_xpra_release.py`](tools/install_xpra_release.py) is a standalone
standard-library installer for Debian 13 and Ubuntu 26.04. It resolves the
newest canonical package release from `kogeler/xpra:develop`, verifies archive
and DEB contents before mutation, prints the exact installed Xpra inventory,
and requires interactive confirmation before purging that inventory. It uses
APT for the validated local packages and dependencies.

Elsewindow deliberately uses this maintained fork rather than treating a
generic upstream Xpra build as interchangeable. Project development uncovered
dozens of blocking Xpra defects: some fixes were accepted as direct upstream
contributions, some were implemented by the upstream maintainer after issue
reports, and some were not accepted after technical disagreements. The last
category requires continued maintenance of additional fork patches. The
[Xpra guide](doc/xpra.md#why-the-maintained-xpra-fork-is-required) explains
the resulting user compatibility boundary.

Review the installer and the [security contract](doc/security.md). The streamed
route below is pending until the first reviewed `main` push; it must not be
treated as available before then:

```bash
( set -o pipefail; curl --proto '=https' --tlsv1.2 -fsSL 'https://raw.githubusercontent.com/kogeler/elsewindow/main/tools/install_xpra_release.py' | /usr/bin/python3 - )
```

Do not prefix the pipeline or Python process with `sudo`. When privilege is
needed, the reviewed script reads purge confirmation from the controlling
terminal and invokes `/usr/bin/sudo` itself only for its root-owned staging and
APT transaction.

## Standalone Linux Executables

After the first release, GitHub Releases provide `elsewindow-linux-amd64` and
`elsewindow-linux-arm64`. These native one-file ELF executables bundle
Elsewindow, CPython, `ssh-wrapper`, version metadata, and the reviewed YAML
profiles, and the local Xpra setup code and dependency lock. They do not bundle
OpenSSH, Xpra, Podman, GPU drivers, VA-API, or
distribution packages. Verify the release's `SHA256SUMS.txt`, make the selected
file executable, run `./elsewindow-linux-amd64 --prepare-xpra`, then
`./elsewindow-linux-amd64 --diagnose` (or the arm64 equivalents) before starting
a session. Preparation uses the system Xpra Python, not the bundled interpreter,
and needs neither a checkout nor GNU Make. See the
[setup reference](doc/cli.md#prepare-xpra) for prerequisites and storage.

## Documentation

- [Rendered documentation site](https://kogeler.github.io/elsewindow/)
  (published by the first reviewed direct `main` push)
- [Getting started](doc/getting-started.md)
- [Complete CLI reference](doc/cli.md)
- [Xpra behavior and lifecycle](doc/xpra.md)
- [Security model](doc/security.md)
- [Architecture](doc/architecture.md)
- [Development and validation](doc/development.md)
- [Contributing](doc/contributing.md)
- [Maintainer CI contract](doc/maintenance/CI.md)
- [Dependency maintenance](doc/maintenance/DEPENDENCIES.md)
- [Release maintenance](doc/maintenance/RELEASES.md)

The Pages workflow renders these same maintained Markdown files and validates
every generated route, local link, anchor, canonical URL, sitemap, crawler
file, and advertised `llms.txt` route without network access.

## Automatic Live Validation

`make live-test` resolves the newest fork release, builds checksum-bound Ubuntu
target and Debian client images, and runs ordinary and persistent lifecycle cases
on a private rootless Podman network. The payload excludes `elsewindow/` source
and clean-installs the already verified wheel. The harness generates a fresh
Ed25519 key, performs exactly one authentication per invocation, verifies a visible
window and picture updates through the production profile assembler, preserves
an unrelated session, and removes only its labelled containers and network.
All wheel, test, and key material enters through validated tar streams; the
test uses no host bind, data volume, or copy channel.

Persistent cases exercise real systemd user services, linger consent, resumption
after client/SSH loss, stable application identity and cleanup on application
exit. The same matrix checks all four local/remote log sources in the local
journal and terminal, and only remote sources in the server journal, at the
selected log level. Enable this opt-in mode with
`--persistent`; see the [CLI reference](doc/cli.md#persistent).

Released under the [MIT License](LICENSE).
