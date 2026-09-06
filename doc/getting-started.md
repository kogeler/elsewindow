# Getting Started

## Requirements

- CPython 3.13 or 3.14 with `venv`;
- GNU Make, OpenSSH, and `false` locally;
- access to the published
  [`ssh-wrapper==0.1.0`](https://pypi.org/project/ssh-wrapper/0.1.0/) wheel when
  preparing the runtime;
- the reviewed maintained-fork Xpra packages locally and remotely;
- local system Python venv support (`python3-venv` on the supported systems);
- an accessible systemd journal on both hosts;
- a local graphical session and a remote Linux account capable of running the
  Wayland Xpra server.

Install the maintained package build with the reviewed
[`install_xpra_release.py`](../tools/install_xpra_release.py) helper when the
system does not already have a compatible Xpra installation.
Generic upstream packages are not a supported substitute. See [why the
maintained fork is required](xpra.md#why-the-maintained-xpra-fork-is-required)
and which upstream fixes were accepted, issue-driven, or retained only in the
fork.

On amd64 Debian 13 or Ubuntu 26.04, review the helper before use. The streamed
route is pending until the first reviewed `main` push:

```bash
( set -o pipefail; curl --proto '=https' --tlsv1.2 -fsSL 'https://raw.githubusercontent.com/kogeler/elsewindow/main/tools/install_xpra_release.py' | /usr/bin/python3 - )
```

Do not run the pipeline under `sudo`. The helper confirms the exact installed
Xpra inventory through `/dev/tty`, validates the complete release and DEB
capability payload, then invokes `/usr/bin/sudo` itself. It installs the local
DEBs and their dependencies with APT rather than `dpkg -i`.

## Install The Python Command

After the first release, use either normal pip or pipx installation:

```bash
python3.14 -m pip install "elsewindow==0.2.0"
# or: pipx install "elsewindow==0.2.0"
elsewindow --help
elsewindow --prepare-xpra
elsewindow --diagnose
```

## Prepare A Source Checkout

From this project directory:

```bash
make runtime-venv
./bin/elsewindow --help
```

The one make target prepares `venv-runtime` and a separate `venv-xpra` using the
system Xpra interpreter. Ordinary launcher startup never creates an environment
or invokes pip. It refuses to start when either environment is missing or stale.
See the [explicit setup command](cli.md#prepare-xpra) for the installed-package
equivalent and the current-input validation contract.

After the first release, GitHub Releases also provide
`elsewindow-linux-amd64` and `elsewindow-linux-arm64`. Download the file that
matches `uname -m` (`x86_64` means amd64; `aarch64` means arm64), verify it
against the release's `SHA256SUMS.txt`, and set mode `0755`. The executable
bundles Python, Elsewindow, `ssh-wrapper`, and both YAML resources; it still
requires system OpenSSH, `false`, Xpra, a graphical session, GPU/VA-API support
where selected, and distribution packages.

```bash
chmod 0755 ./elsewindow-linux-amd64
./elsewindow-linux-amd64 --prepare-xpra
./elsewindow-linux-amd64 --diagnose
./elsewindow-linux-amd64 --ssh-alias agents-a -- xterm
```

## Start An Application

Standalone setup uses its bundled dependency lock to prepare a separate local
Xpra venv. It does not require this repository, Make, an Elsewindow venv, or
installation into the system Python. Subsequent sessions only validate the
environment; system Xpra and its Python venv support remain prerequisites.

With a trusted OpenSSH alias:

```bash
elsewindow --ssh-alias agents-a -- xterm
```

With a direct host:

```bash
elsewindow --host host.example --user desktop-user -- /usr/bin/xterm
```

Application arguments begin after `--`. To request the reviewed adaptive-alpha
H.264 profile on the default gigabit LAN network profile while allowing only
local-to-remote clipboard synchronization:

```bash
elsewindow \
  --ssh-alias agents-a \
  --encoding-profile h264 \
  --network-profile gigabit_lan \
  --clipboard=to-server \
  -- /opt/application/bin/application
```

The [CLI reference](cli.md) owns every option, default, allowed value, and
combination rule, including [clipboard policy](cli.md#clipboard),
[logging](cli.md#log-level), and [persistent sessions](cli.md#persistent).
See [the Xpra guide](xpra.md) for runtime behavior, failure codes, and
ownership limits.
