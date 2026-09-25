# Elsewindow Application

`elsewindow` starts one remote GUI application and displays its windows
through Xpra. It uses the published
[`ssh-wrapper`](https://pypi.org/project/ssh-wrapper/) package for its
single-master, mux-only OpenSSH lifecycle.

## Why The Maintained Xpra Fork Is Required

Elsewindow's supported Xpra packages come from the maintained
[`kogeler/xpra` fork](https://github.com/kogeler/xpra/tree/develop), not from an
interchangeable upstream package build. During Elsewindow's implementation,
the required Wayland, GPU, codec, rendering, and remote-application topology
exposed dozens of Xpra defects that prevented the project from operating in
the intended form.

Those defects had three outcomes. Some fixes were contributed directly and
accepted upstream. The upstream maintainer fixed others after reports were
filed as issues. Some required changes were not accepted; the remaining
technical disagreements made continued maintenance of the project fork
necessary. The fork therefore carries additional patches required by the
reviewed Elsewindow profiles and release-backed live tests.

This is an operational compatibility boundary, not a claim that every Xpra
change belongs in Elsewindow. The fork owns its internal patch, codec, GPU,
application, and rendering tests. Elsewindow consumes an immutable fork
release and tests its public commands and complete SSH-owned lifecycle. Users
should not replace that release with a generic upstream build and assume the
same behavior.

## Requirements

Run `make runtime-venv` before using the launcher. The local and remote systems
need OpenSSH, `false`, Python 3, and the compatible maintained-fork Xpra
packages, plus an accessible systemd journal on each host. Notification support
requires the distribution's `dbus-daemon` and `python3-dbus`; the project installer
installs both explicitly. The remote Xpra
installation must provide the `seamless` Wayland server. The local side needs
a working graphical session. The `h264` profile
additionally requires a usable DRM render node and a hardware-specific VA-API
driver on both systems.

This repository consumes checksum-bound DEB archives published by
`kogeler/xpra`. `tools/install_xpra_release.py` can install the newest owned
release on Debian 13 or Ubuntu 26.04; see
[Development](development.md#standalone-system-installer). The launcher
itself never installs or changes packages. The installer validates the actual
DEB payload for the native libva encoder and decoder, libyuv, GTK OpenGL,
Wayland, and X11 modules before it can purge an existing installation, and
imports the required modules after APT installs them.

## Usage

See the [CLI reference](cli.md) for the complete syntax, every option, default,
allowed value, and combination rule. A basic invocation uses a trusted
OpenSSH alias:

```bash
elsewindow --ssh-alias workstation -- xterm
```

The compositor atomically chooses a free `wayland-N` socket and publishes it
through `displayfd`. The launcher validates the owner-controlled session
metadata before attaching. Ordinary session names come from the normalized
executable basename; persistent names come from the account-local session key.
Local windows keep the titles published by the remote application.

## Reviewed Xpra Profiles

Two files in `kogeler/xpra:develop` are the canonical mutable configuration:

- [`fork-maintenance/live-cli.yml`](https://github.com/kogeler/xpra/blob/develop/fork-maintenance/live-cli.yml)
  owns the static server/client base, lifecycle, command, diagnostic, encoding,
  encoder, decoder, CSC, and OpenGL blocks;
- [`fork-maintenance/profiles.yml`](https://github.com/kogeler/xpra/blob/develop/fork-maintenance/profiles.yml)
  owns every client-side quality/network profile and its default.

This package ships byte-identical local mirrors at
[`elsewindow/live-cli.yml`](../elsewindow/live-cli.yml) and
[`elsewindow/profiles.yml`](../elsewindow/profiles.yml), because an installed
launcher cannot depend on a fork checkout or a network request. The strict
standard-library loader is the only runtime reader. Python and tests do not
copy the YAML option or numeric tables: tests compare the complete parsed
blocks with the assembled production argv. The same assembler supplies the
live version, server-info, and detach commands, translating only the selected
Wayland display, owned runtime paths, SSH URI, and mux wrapper. Before changing
or reviewing an Xpra argument, first read both current `develop` files and
compare the mirrors byte-for-byte; synchronize drift before changing the
assembler.

The CLI reference owns the [encoding profiles](cli.md#encoding-profile) and
[network profiles](cli.md#network-profile), including the tables checked
against these mirrors. Detailed codec, GPU, alpha-transition, application, and
rendering acceptance remains exclusively in the fork that produces the package.

The mirrored client base supplies ordinary mouse-wheel forwarding. Production
consumes the canonical base, lifecycle, selected transport, and
selected network blocks exactly. It excludes fork-only diagnostics and helper
commands, translates only the three container-private socket/session paths to
the owned remote runtime, and appends this project's selected clipboard policy,
GUI defaults, remaining auxiliary-data restrictions, and dynamic session/application values.

### Minimal Base Review

The fork's live base retains `--minimal`. Elsewindow appends a small, explicit
GUI policy in `session.py` after the unchanged mirrored blocks: custom cursor
shapes and detected DPI on both peers; mouse-wheel forwarding on both axes,
keyboard-state synchronization and initially unscaled
client scaling on the client. These are defaults, not new public switches.
The mirrored client base enables modal-window handling.
Pointer motion, buttons, focus, resizing and application key combinations
remain ordinary Xpra behavior; raw keycodes and Xpra's own hotkeys stay disabled.
Remote application notifications are also enabled, using the private owned
session bus described in the [security model](security.md).

The complete reviewed set of defaults changed by the fork's current
[`--minimal` parser](https://github.com/kogeler/xpra/blob/develop/xpra/scripts/parsing.py)
is grouped below. The mirrors remain byte-identical; review this boundary again
when changing the consumed Xpra version.

| Affected options | Elsewindow decision |
|---|---|
| `cursors`, `mousewheel`, `keyboard-sync`, `modal-windows` | Restore ordinary GUI behavior; wheel forwarding includes both axes. |
| `dpi`, `desktop-scaling` | Detect DPI; permit client scaling starting at 1:1, without automatic zoom. Application/toolkit HiDPI behavior remains its own responsibility. |
| `notifications` | Enable on both peers; the local desktop controls presentation. |
| `dbus`, `dbus-control` | Use the owned private bus remotely and the existing desktop notification bus locally; disable D-Bus control and Xpra bus autolaunch on both peers. |
| `clipboard` | Apply the selected symmetric clipboard policy, bidirectional by default. |
| `video`, `encodings`, `encoding`, `opengl` | Use the selected canonical transport profile, not independent GUI overrides. |
| `bandwidth-limit`, `bandwidth-detection` | Use the selected canonical network profile and fixed client base. |
| `pings`, `compression-level` | Retain the minimal transport baseline; the owned SSH master and heartbeat supervise lifetime. |
| `file-transfer`, `open-files`, `open-url`, `forward-xdg-open`, `printing` | Keep disabled; notification support grants no file or URL opening capability. |
| `audio`, `webcam`, `gstreamer`, `bell` | Keep disabled; no device/audio forwarding or system bell. |
| `xsettings`, `gsettings-sync` | Keep desktop-settings synchronization disabled; do not copy the local desktop's configuration into the remote account. |
| `start-new-commands`, `mdns`, `ssl-upgrade`, `websocket-upgrade`, `ssh-upgrade`, `rfb-upgrade`, `rdp-upgrade` | Keep disabled; no extra command startup, discovery, or transport upgrades. |
| `mmap`, `sharing`, `lock`, `remote-logging` | Keep the minimal policy; use SSH transport and the product's own labeled journal stream, without enabling Xpra sharing or extra session locking. |
| `system-tray` | Load the client tray helper required by the current notification presenter; explicitly disable application tray forwarding on the server. |
| `tray`, `splash`, `headerbar`, `border` | Keep Xpra's extra UI disabled. Native window decorations are unaffected. |
| `key-shortcut`, `keyboard-raw` | Keep Xpra hotkeys and raw keycodes disabled, preserving application shortcuts and translated keyboard input. |
| `windows`, `min-size`, `max-size`, `desktop-fullscreen` | Keep seamless windows enabled and the permissive size baseline; do not force a full-desktop mode. This does not disable an application's own fullscreen action. |
| `pixel-depth`, `sync-xvfb` | Keep the reviewed true-color baseline; Xvfb synchronization does not apply to the remote Wayland backend. |
| client `bind` | Keep the minimal attach listener policy; no extra Xpra listener. |

The current fork has a separate startup limitation: its minimal-mode reparse
can duplicate an append-valued `start-child-after-connect` command. Ordinary
sessions may therefore invoke an application twice; an application's own
single-instance behavior can conceal this. The public live GUI fixture is
single-instance, but this is not a product workaround or a guarantee that an
arbitrary application starts only once. That parser behavior belongs to the
maintained fork; the GUI defaults do not change the startup lifecycle.

Likewise, allowing modal hints on the client does not create missing Wayland
metadata. The current release's GTK Wayland dialog can be displayed without
the X11 modal/transient properties on the local window. The live fixture checks
opening and keyboard dismissal; full dialog parenting/stacking correctness
remains a maintained-fork concern.

The application itself runs on the remote Wayland display. Its Vulkan or
OpenGL renderer opens the remote GPU and renders there; Xpra captures the
resulting window image and transports picture updates plus control and input
and clipboard data over the owned SSH mux. The local machine never receives the
application's GL or Vulkan command stream and never receives access to the
remote render node.

## System Journal

In ordinary and persistent sessions, the local terminal and journal aggregate
four explicitly labeled sources: `elsewindow-local`, `elsewindow-remote`,
`elsewindow-xpra-local`, and `elsewindow-xpra-remote`. The remote journal contains
only the two remote sources. Select verbosity through the
[logging option](cli.md#log-level); that reference describes all levels,
local stdout/stderr routing, and the sensitive-data warning.

Read logs locally on either host:

```bash
journalctl -t elsewindow-local -t elsewindow-remote -t elsewindow-xpra-local -t elsewindow-xpra-remote --since today
journalctl -t elsewindow-remote -t elsewindow-xpra-remote -f
```

Records carry `ELSEWINDOW_SIDE`, `ELSEWINDOW_SESSION`, `ELSEWINDOW_LOG_LEVEL`,
`ELSEWINDOW_CATEGORY`, `ELSEWINDOW_COMPONENT`, `ELSEWINDOW_FORWARDED`, and
`ELSEWINDOW_SOURCE_PID`. Use
`ELSEWINDOW_SESSION=<session-id>` to select one session on either host:

```bash
journalctl 'ELSEWINDOW_SESSION=<session-id>' -f
```

Replace `<session-id>` with the ID printed in `[session=...]` in every message.
Ordinary invocations receive unique IDs. Persistent IDs derive from the remote
machine identity, UID, and canonical executable/argument key, so reconnects keep
one ID without mixing different hosts or accounts. This log namespace does not
change persistent application lookup. Multiple clients for one persistent
application share its ID and receive their own live log subscriptions.
Local startup probes send diagnostics to the local journal even when SSH is
never opened. Persistent startup buffers at most 256 KiB until identification;
failed identification flushes those records with the unique invocation ID.

New remote records travel over dedicated channels of the already authenticated
SSH master. Subscription starts before remote probes or application startup;
resuming a persistent session subscribes to its stable session key before
attaching. No second authentication, reconnection, privileged journal reader,
or TCP log listener is used. Remote helpers publish to private bounded Unix
datagram queues; those queues are not log files and are removed when their
owned SSH observer exits. Slow or disconnected observers do not stop the
application. Reported transport gaps can be investigated in the server journal.
Records produced while disconnected are retained only by the server journal;
the next connection subscribes to new records without replaying history.

Journald must be available on both hosts before startup. Delivery failures
during a session are reported without changing application lifetime. Journal
access, rate limits, retention and persistence across reboot remain host policy;
Elsewindow does not change them or install packages. Individual records and
partial lines are bounded. Unstructured stdout defaults to info and stderr to
warning; traceback continuations inherit the preceding Xpra severity.

## Lifecycle And Isolation

Ordinary sessions use a heartbeat-owned process group. The opt-in
[persistent option](cli.md#persistent) uses a resumable user service; its
reference covers session identity, compatible reconnect settings, linger
consent, and lifetime limits.

One invocation:

1. validates authority, timeouts, application argv, and local Xpra options;
2. opens one deliberate foreground OpenSSH ControlMaster;
3. validates the remote public `seamless` command and required options through
   that master's mux socket;
4. starts a foreground Wayland Xpra server in one heartbeat-supervised remote
   process group, or creates/resumes the verified persistent user service;
5. reads and validates the display selected through `displayfd`;
6. attaches one local Xpra client through the same mux socket.

The server cannot adopt an existing display. It binds no Xpra TCP listener.
OpenSSH forwarding, agent sharing, X11 forwarding, automatic reconnection,
fallback authentication, Xpra audio, webcam, printing, file transfer, URL and
file opening, HTML, SSH upgrades, D-Bus control, and additional command
startup are disabled. Clipboard synchronization follows the validated public
policy and defaults to `both`; notifications use a private session bus and the
same owned SSH/Xpra connection.

In ordinary mode, normal detach, application exit, cancellation, `SIGHUP`,
`SIGINT`, `SIGTERM`, lease expiry, or SSH-master loss enters the same idempotent
cleanup. The local client
stops first. The remote supervisor then terminates only its recorded process
group and removes its sockets and private runtime state. It never searches by
executable name or modifies an unrelated Xpra session.

In persistent mode, those local disconnect paths leave the remote service
alone. Only the foreground application's exit ends the normal remote lifetime.
See [timeouts and intervals](cli.md#timeouts-and-intervals) for the timing
controls that apply to each mode.

Applications that double-fork, move to another cgroup or session, or delegate
to an already-running single-instance process can escape this ownership
boundary. Use an application option that creates a fresh foreground instance.

## Errors And Diagnostics

Stable failures include `missing_dependency`, `connection_start_failed`,
`connection_lost`, `xpra_probe_timeout`, `xpra_ready_timeout`,
`xpra_identity_mismatch`, `xpra_client_failed`, and `xpra_server_exited`.
Public errors omit private control paths and full subprocess argv. Selected
Xpra diagnostics are bounded, stripped of control characters, and scrubbed of
private runtime paths.

## Release-backed Image Verification

`make xpra-images` freshly resolves the newest canonical `kogeler/xpra`
package release, validates both distribution archives, and prepares matching
Ubuntu 26.04 target and Debian 13 client images. Build inputs and connection
material are streamed through validated archives; no host path is bind-mounted
and `podman cp` is not used. Consumers execute the images by immutable image ID
and can require the same release ID, commit, version, package set, and asset
provenance on both sides.

`make live-test` first builds and verifies the Python distribution. Its client
payload excludes the source package, installs that wheel into the disposable
account, and asserts the installed module path, version metadata, wrapper
version, and both resources before any lifecycle case. Thus the live result is
packaged-product evidence, not an editable-checkout result.

Codec, GPU, application, and direct-Xpra behavioral acceptance belongs to
`kogeler/xpra:develop` under `fork-maintenance/`. This project verifies the
profile loader, exact argv assembly, owned SSH/Xpra lifecycle, package release
contract, and image provenance without redefining the fork's codec tests.
