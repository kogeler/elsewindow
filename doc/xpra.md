# Elsewindow Application

`elsewindow` starts one remote GUI application and displays its windows
through Xpra. It uses the published
[`ssh-wrapper`](https://pypi.org/project/ssh-wrapper/0.1.0/) package for its
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
packages. The remote Xpra installation must provide the `seamless` Wayland
server. The local side needs a working graphical session. The `h264` profile
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

Inspect installed identities and prerequisites without connecting:

```bash
elsewindow --diagnose
```

Use a trusted OpenSSH alias:

```bash
elsewindow --ssh-alias workstation -- xterm
```

Or provide one direct authority:

```bash
elsewindow \
  --host host.example \
  --user desktop-user \
  --port 2222 \
  -- xterm
```

The application arguments begin after `--`. They are validated as data and
converted once to Xpra's `--start-child-after-connect` value. The application
starts only after the graphical client connects.

Choose one reviewed encoding profile and, when needed, one network profile:

```bash
elsewindow \
  --ssh-alias workstation \
  --encoding-profile h264 \
  --network-profile mobile_5g \
  -- /opt/application/bin/application
```

The encoding default is `rgb`. The network default is the
`default_profile` declared by the mirrored configuration, currently
`gigabit_lan`. The user does not select an Xpra backend, display, session name,
title, individual encoder, decoder, colorspace converter, pixel format, or
renderer. The remote backend is always Wayland.

The compositor atomically chooses a free `wayland-N` socket and publishes it
through `displayfd`. The launcher validates the owner-controlled session
metadata before attaching. The session name comes from the normalized basename
of the executable, while local windows keep the titles published by the remote
application.

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

The public encoding mapping is deliberately small:

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

For H.264, the launcher checks the public local `xpra opengl` result before
starting the session. Detailed packet, alpha-transition, VA-API, libyuv,
NV12-presentation, application, and rendering acceptance remains exclusively
in the fork that produces the package; this repository consumes the resulting
reviewed profile without reproducing internal Xpra probes.

The network profiles supply only client-side minimum quality, minimum speed,
auto-refresh delay, refresh rate, and bandwidth limit. This table is checked
against the mirrored YAML so documentation drift fails the repository tests:

<!-- BEGIN GENERATED XPRA NETWORK PROFILES -->
| Profile | Minimum quality | Minimum speed | Auto refresh | Refresh rate | Bandwidth limit |
|---|---:|---:|---:|---:|---:|
| `gigabit_lan` (default) | 90 | 90 | 0.10 s | 60 Hz | unlimited (`0`) |
| `fast_wired` | 82 | 80 | 0.20 s | 60 Hz | 50 Mbps |
| `mobile_5g` | 78 | 75 | 0.30 s | 45 Hz | 25 Mbps |
| `mobile_4g` | 68 | 70 | 0.45 s | 30 Hz | 8 Mbps |
| `power_saving` | 72 | 70 | 0.50 s | 30 Hz | 8 Mbps |
<!-- END GENERATED XPRA NETWORK PROFILES -->

Production consumes the canonical base, lifecycle, selected transport, and
selected network blocks exactly. It excludes fork-only diagnostics and helper
commands, translates only the three container-private socket/session paths to
the owned remote runtime, and appends this project's no-forwarding and
auxiliary-data restrictions plus dynamic session/application values.

The application itself runs on the remote Wayland display. Its Vulkan or
OpenGL renderer opens the remote GPU and renders there; Xpra captures the
resulting window image and transports picture updates plus control and input
over the owned SSH mux. The local machine never receives the application's GL
or Vulkan command stream and never receives access to the remote render node.

## Lifecycle And Isolation

One invocation:

1. validates authority, timeouts, application argv, and local Xpra options;
2. opens one deliberate foreground OpenSSH ControlMaster;
3. validates the remote public `seamless` command and required options through
   that master's mux socket;
4. starts a foreground Wayland Xpra server in one heartbeat-supervised remote
   process group;
5. reads and validates the display selected through `displayfd`;
6. attaches one local Xpra client through the same mux socket.

The server cannot adopt an existing display. It binds no Xpra TCP listener.
OpenSSH forwarding, agent sharing, X11 forwarding, automatic reconnection,
fallback authentication, Xpra audio, webcam, printing, file transfer, URL and
file opening, notifications, HTML, SSH upgrades, D-Bus, and additional command
startup are disabled.

Normal detach, application exit, cancellation, `SIGINT`, `SIGTERM`, lease
expiry, or SSH-master loss enters the same idempotent cleanup. The local client
stops first. The remote supervisor then terminates only its recorded process
group and removes its sockets and private runtime state. It never searches by
executable name or modifies an unrelated Xpra session.

Applications that double-fork, move to another cgroup or session, or delegate
to an already-running single-instance process can escape this ownership
boundary. Use an application option that creates a fresh foreground instance.

## Options And Errors

The lifecycle timeout options are:

| Option | Default |
|---|---:|
| `--connect-timeout` | `120` seconds |
| `--ready-timeout` | `45` seconds |
| `--probe-timeout` | `8` seconds |
| `--poll-interval` | `1` second |
| `--heartbeat-interval` | `10` seconds |
| `--lease-timeout` | `45` seconds |
| `--cleanup-grace` | `5` seconds |

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
