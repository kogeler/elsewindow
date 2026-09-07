# CLI Reference

This is the complete reference for the public `elsewindow` command. The
installed console command, repository launcher, `python -m elsewindow`, and
standalone executables accept the same options. See
[Getting started](getting-started.md) for installation and the
[Xpra guide](xpra.md) for runtime requirements,
ownership, and troubleshooting.

## Invocation

Choose one SSH authority, then put the remote executable and its arguments
after `--`:

```bash
elsewindow [OPTIONS] --ssh-alias ALIAS -- APP [ARG ...]
elsewindow [OPTIONS] --host HOST --user USER [--port PORT] -- APP [ARG ...]
```

All Elsewindow options go before the application. Values after `--` belong to
the application, even when they look like Elsewindow flags. Quote arguments
containing whitespace in the local shell. Argument boundaries and empty
arguments are preserved; the application is passed as argv, not as an
operator-supplied shell script.

The executable must be nonempty. The complete application argv is limited to
256 items and 16,384 UTF-8 bytes in total; NUL bytes are rejected. Executables
without a slash are resolved on the remote `PATH`; paths with a slash refer
to the remote host, not the client. Use an application mode that stays in the
foreground instead of delegating to an existing instance.

## All Options

`disabled` means that the flag is absent. `—` means there is no default value.
The option inventory, defaults, and allowed choice tables are checked against
the current CLI parser by the test suite.

| Option | Default | Description |
|---|---|---|
| `-h`, `--help` | — | Print help and exit without starting a session. |
| `--version` | — | Print the Elsewindow version and exit. |
| `--diagnose` | disabled | Report packaged identities and local prerequisites without connecting. |
| `--prepare-xpra` | disabled | Prepare the isolated local Xpra Python environment without connecting. |
| `--ssh-alias` | — | Select a trusted OpenSSH host alias. |
| `--host` | — | Select a direct remote hostname or IP address; requires `--user`. |
| `--user` | — | Select the remote login user for `--host`. |
| `--port` | `22` | Select the direct SSH port; alias ports come from OpenSSH configuration. |
| `--encoding-profile` | `rgb` | Select a reviewed pixel transport profile. |
| `--network-profile` | `gigabit_lan` | Select the attaching client's quality and network policy. |
| `--clipboard` | `both` | Select clipboard synchronization on both peers. |
| `--persistent` | disabled | Keep the remote application after disconnect and resume it by argv. |
| `--log-level` | `warning` | Select one Elsewindow/Xpra logging policy on both hosts and for local terminal output. |
| `--connect-timeout` | `120` s | Limit startup of the owned SSH master, including authentication. |
| `--ready-timeout` | `45` s | Limit waiting for the owned remote Xpra session to become ready. |
| `--probe-timeout` | `8` s | Limit an individual remote session-metadata readiness probe. |
| `--poll-interval` | `1` s | Set the readiness and persistent-service observation interval. |
| `--heartbeat-interval` | `10` s | Set the ordinary session's ownership-heartbeat interval. |
| `--lease-timeout` | `45` s | Set how long an ordinary remote supervisor may wait without a valid heartbeat. |
| `--cleanup-grace` | `5` s | Allow graceful termination of the ordinary remote process group before forced cleanup. |

## Informational Commands

`-h` / `--help` prints the accepted syntax. `--version` prints the version
resolved from the packaged version authority. Neither requires an authority
or application, and neither opens SSH.

`--diagnose` reports the Elsewindow and installed `ssh-wrapper` identities,
SHA-256 digests of the packaged YAML, remote-helper sources and Xpra lock, Linux
support, and resolution of local commands. It also revalidates the prepared
Xpra environment. It exits with status 1 if these checks fail, otherwise 0.
It does not start Xpra, validate GPU rendering, or inspect the remote host.
Run it without a session authority or application:

```bash
elsewindow --diagnose
```

### Prepare Xpra

`--prepare-xpra` explicitly creates or repairs the local Xpra environment using
the system Python and the bundled hash-locked PyOpenGL pair from PyPI. It needs
the distribution's Xpra, Python venv support, GTK and OpenGL libraries already
installed. It does not install system packages or open SSH. This works with the
repository launcher, installed package, and standalone executable. It cannot
be combined with `--diagnose`, a session authority, or an application.

Preparation prefers the accelerator's prebuilt wheel. If none exists for the
system interpreter and architecture (including Linux arm64), it builds the
hash-verified source archive using separately locked temporary build tools.
That route also needs the system C compiler and Python development headers
(`build-essential` and `python3-dev` on Debian/Ubuntu). Preparation never installs
those system prerequisites automatically. The temporary build packages are
removed from the Xpra environment after installation.

The repository's `make runtime-venv` prepares both environments, storing the
Xpra additions in `.venvs/<machine-user-key>/venv-xpra`. Installed and standalone
commands default to the `elsewindow/<machine-user-key>/xpra-venv` directory beneath
the user's XDG data directory. The key is an application-specific hash of `/etc/machine-id`
and the local UID, so a shared data directory keeps each host's interpreter
and native additions separate. A missing or invalid machine ID is an error.
`ELSEWINDOW_XPRA_VENV` can select an exact dedicated absolute directory instead;
an explicit override must not be shared between hosts. Ordinary
startup only validates the prepared environment and fails with setup guidance
if its current lock, system interpreter, or installed package bytes differ.

## SSH Authority

`--ssh-alias ALIAS` uses a trusted OpenSSH configuration entry, including its
user, port, identity, host-key policy, and supported authentication setup.
Do not combine it with `--host`, `--user`, or a direct port override. Configure
an alias's port in OpenSSH itself.

Alternatively, use `--host HOST --user USER`. The host is a hostname or IP
address, not an SSH URI or a combined `user@host` string. `--port PORT` accepts
an integer from 1 through 65,535 and defaults to 22 in direct mode. IPv6
addresses must be unbracketed.

```bash
elsewindow --ssh-alias workstation -- xterm
elsewindow --host host.example --user desktop-user --port 2222 -- xterm
```

Both forms perform one deliberate authentication and use only channels backed
by the owned master. They never reconnect automatically after master loss.
See the [security model](security.md) for the fixed SSH isolation policy.

## Profiles And Clipboard

Only reviewed profiles are selectable. There are no public switches for an
Xpra backend, display, session name, title, individual encoder, decoder,
colorspace converter, pixel format, or renderer. Those choices come from the
packaged profile configuration; the remote backend is always Wayland.

Custom application cursors, vertical and horizontal mouse-wheel forwarding,
keyboard-state synchronization, and modal-window handling are enabled by
default in both session lifetime modes, as are remote application notifications.
The local desktop's notification service presents them; its do-not-disturb,
permissions, and visibility policies still apply. DPI is detected instead of forced to
a fixed value. Client scaling is allowed but starts at 1:1; there is no automatic
zoom or extra Elsewindow CLI switch for these ordinary GUI features. See the
[minimal-base review](xpra.md#minimal-base-review) for the retained restrictions.

### Encoding Profile

`--encoding-profile PROFILE` selects the pixel transport used by both peers.
The default is `rgb`.

<!-- BEGIN GENERATED XPRA ENCODING PROFILES -->
| CLI value | Canonical transport and policy |
|---|---|
| `rgb` | `rgb.strict` |
| `h264` | `h264.adaptive-alpha` |
<!-- END GENERATED XPRA ENCODING PROFILES -->

The strict RGB policy disables video codecs and CSC and uses the non-OpenGL
client path. The adaptive-alpha H.264 policy selects native libva H.264,
server-side libyuv, and native client OpenGL while retaining the canonical
alpha-capable and lossless alternatives declared by the fork.

H.264 requires usable DRM render nodes and hardware-specific VA-API drivers
on both hosts. Before SSH startup, Elsewindow checks the public local
`xpra opengl` result. Detailed codec, alpha, GPU, and application acceptance
belongs to the [maintained fork](xpra.md#why-the-maintained-xpra-fork-is-required).

### Network Profile

`--network-profile PROFILE` selects the attaching client's minimum quality,
minimum speed, auto-refresh delay, refresh rate, and bandwidth limit.
The default is `gigabit_lan`.
The complete table is checked against the packaged YAML mirror:

<!-- BEGIN GENERATED XPRA NETWORK PROFILES -->
| Profile | Minimum quality | Minimum speed | Auto refresh | Refresh rate | Bandwidth limit |
|---|---:|---:|---:|---:|---:|
| `gigabit_lan` (default) | 90 | 90 | 0.10 s | 60 Hz | unlimited (`0`) |
| `fast_wired` | 82 | 80 | 0.20 s | 60 Hz | 50 Mbps |
| `mobile_5g` | 78 | 75 | 0.30 s | 45 Hz | 25 Mbps |
| `mobile_4g` | 68 | 70 | 0.45 s | 30 Hz | 8 Mbps |
| `power_saving` | 72 | 70 | 0.50 s | 30 Hz | 8 Mbps |
<!-- END GENERATED XPRA NETWORK PROFILES -->

A persistent session may be resumed with a different network profile because
this option affects only the new local client.

### Clipboard

`--clipboard POLICY` applies the clipboard policy explicitly to both Xpra
peers. The default is bidirectional `both`.

| Value | Synchronization |
|---|---|
| `off` | Disabled on the local client and remote server. |
| `to-server` | Local client to remote server only. |
| `both` | Bidirectional. |

Clipboard contents cross the owned SSH mux as Xpra protocol data; no
forwarding socket is opened. Use a narrower policy when data must not cross
that trust boundary. See the [security model](security.md) and
[clipboard logging](#log-level) before debugging with sensitive data.

```bash
elsewindow --ssh-alias workstation --encoding-profile h264 \
  --network-profile mobile_5g --clipboard=to-server \
  -- /opt/application/bin/application
```

## Session Lifetime

### Persistent

`--persistent` starts or resumes an owned transient user systemd service.
Without it, the ordinary heartbeat-supervised session ends on client or SSH
loss. In persistent mode, client exit and SSH loss leave the remote
application running. Repeat the same invocation to resume it:

```bash
elsewindow --persistent --ssh-alias workstation -- /opt/application/bin/application
```

The session key uses the absolute remote executable invocation path and exact
ordered arguments, including empty arguments, within the remote account and
host. Changing arguments creates a different session. Executable symlinks are
preserved, including virtualenv interpreters and links to versioned programs.
Names without a slash are resolved on remote `PATH` before deriving the key.

An existing session must retain its encoding, clipboard, and logging policy.
An incompatible reconnect is refused without stopping or replacing it; use
the original settings or exit the application first. The network profile may
change because it applies only to the attaching client. Services created
before journal support cannot be adopted.

The remote account must have a working user systemd manager and linger.
Elsewindow checks this on every invocation. If linger is off, the invoking
terminal must supply exact `y` or `yes` before Elsewindow enables it for that
account, using remote `sudo` when required, and verifies the result. A refusal
or missing terminal starts no application and leaves the account unchanged.
Linger remains enabled afterwards and affects the account's other user
services too; ask the administrator to disable it when no longer wanted.

Persistent sessions survive detach, client crashes, terminal closure, and SSH
loss, but not host reboot, an administrative service stop, or an Xpra crash.
Exiting the foreground application with any status ends its session. There
is no automatic reconnect or restart; each manual invocation authenticates
once. The application starts immediately under the service, so a failed first
attach may leave it running. Retry the same command to inspect and resume it.
Ordinary sessions start the application only after the graphical client
connects. See [lifecycle and isolation](xpra.md#lifecycle-and-isolation).

## Logging

### Log Level

`--log-level LEVEL` selects the same logging policy for Elsewindow and Xpra
on both hosts. The default is `warning`.

| Value | Included records |
|---|---|
| `critical` | Critical failures only. |
| `error` | Errors and critical failures. |
| `warning` | Warnings, errors, and critical failures. |
| `info` | Informational records and all more severe messages, including Elsewindow lifecycle events. |
| `debug` | General Xpra debugging and all more severe records. |
| `debug-clipboard` | Clipboard diagnostics in addition to the warning baseline, including local XFixes selection events. |

The local journal and terminal contain four explicitly marked sources:

| Source | Journal identifier and terminal prefix |
|---|---|
| Local Elsewindow | `elsewindow-local` |
| Remote Elsewindow helper | `elsewindow-remote` |
| Local Xpra | `elsewindow-xpra-local` |
| Remote Xpra | `elsewindow-xpra-remote` |

Every message includes `[session=<id>]`; the same ID is stored in the native
`ELSEWINDOW_SESSION` journal field on both hosts. Ordinary invocations have
unique IDs. Persistent IDs remain stable across reconnects and distinguish
remote machines, accounts, and executable/argument combinations. Concurrent
clients observing the same persistent application share its session ID;
`ELSEWINDOW_SOURCE_PID` distinguishes their producing processes.

The server writes only its two remote sources to its journal. New remote
records travel to the client through the existing authenticated SSH master.
History produced during a disconnection is not replayed. Locally the same
filtered records go to the journal and terminal: warning/error/critical to
stderr, and info/debug to stdout. There is no separate terminal verbosity.
Both debug modes warn that logs may expose clipboard contents or other sensitive data;
use only non-sensitive test data.
Persistent startup briefly buffers early local diagnostics until the remote
identity is known, then writes them with that same ID. If startup fails before
identification, its diagnostics use one unique invocation ID instead.

```bash
elsewindow --log-level=debug-clipboard --ssh-alias workstation -- xterm
```

A persistent service retains its original level; reconnect with that same
level or exit the application before starting with a new policy. A service
created by an older helper without log forwarding must also exit before this
logging protocol can be used; the client never silently replaces it. See
[the system journal](xpra.md#system-journal) for reading records, session
filters, availability requirements, and host-controlled retention.

## Timeouts And Intervals

All seven timing options in the [option table](#all-options) accept finite
seconds from 0.1 through 900, inclusive. Fractional values are supported.
`--lease-timeout` must be strictly greater than twice
`--heartbeat-interval`. These values are validated before opening SSH.

`--connect-timeout` applies to each new owned SSH master.
`--ready-timeout` bounds the readiness-wait phase after the server or service
has been started or selected; `--probe-timeout` limits each remote
session-metadata query in that phase. They are not global launch deadlines:
capability checks, persistent control operations, and linger consent have
their own handling. `--poll-interval` controls readiness polling and repeated
observation of a persistent service, not the Xpra frame rate.

`--heartbeat-interval`, `--lease-timeout`, and `--cleanup-grace` govern the
ordinary remote process group. Lease expiry leads to owned cleanup; the
grace period allows termination before escalation. They do not configure
the persistent service, although the same CLI validation still applies.
Connection, readiness, and probe timeouts remain relevant to each persistent
attach attempt. No timing option enables reconnection or extends ownership
to unrelated resources.

See [errors and diagnostics](xpra.md#errors-and-diagnostics) for runtime
failure codes and the [security model](security.md) for cleanup boundaries.
