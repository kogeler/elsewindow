# Security Model

The user directly selects the SSH authority and remote application argv. The
selected remote account and application are trusted to access everything that
account normally can. Standard OpenSSH configuration, host keys, agents,
ProxyJump configuration, and hardware-token interaction remain trusted input.

One invocation owns one foreground OpenSSH ControlMaster. Every secondary
operation requires its mux socket and is configured so it cannot authenticate
or reconnect independently. OpenSSH forwarding, agent sharing, X11 forwarding,
and configured local or remote commands are disabled. Xpra TCP, HTML, SSH
upgrade, audio, webcam, printing, file transfer, URL opening,
and automatic reconnection are disabled.

Application notifications and clipboard synchronization are the reviewed
auxiliary data channels. Notifications are forwarded by default and can expose
remote application titles, message bodies and icons on the local desktop;
notification action/close responses return only through the owned Xpra channel.
The [clipboard policy](cli.md#clipboard) defaults to bidirectional
synchronization. Clipboard data is visible to the trusted remote account and
application; narrow or disable synchronization when the local clipboard may
contain data that should not cross that trust boundary.

Each remote session owns a separate foreground `dbus-daemon` on a private Unix
socket. It never adopts the account's desktop/user bus or forwards arbitrary
D-Bus traffic. The daemon inherits the ordinary heartbeat process group or the
persistent service cgroup and is explicitly reaped by the session supervisor.
Xpra preserves only this supplied bus, does not launch another, and keeps
D-Bus remote control disabled. Concurrent sessions have distinct buses;
persistent reconnects retain the application's original bus.
The bus has no service-activation directories. Elsewindow starts only its owned
portal, GTK backend, and transient permission store; it never activates a host
document portal, mounts a FUSE filesystem, or adopts the user desktop's services.
Portal helpers use in-memory settings and an inert system-bus address, so they
cannot activate host settings, device, or realtime services during startup.
Portal method calls are limited to remote file selection and notifications,
their request cancellation, and D-Bus metadata. Other portal methods, including
OpenURI and the GTK backend's unrelated interfaces, are denied by the private
bus policy. This does not grant local file access or URL opening.
The local Xpra client uses its existing desktop bus to present notifications;
its D-Bus control and bus autolaunch are also disabled.
The current fork's notification presenter requires its client tray helper module.
Elsewindow loads that module but explicitly disables server-side application tray
forwarding and keeps Xpra's own tray icon hidden.

Both targeted clipboard and general [debug logging](cli.md#log-level) can
include sensitive data. Both choices warn; `warning` is the default. Persistent
startup defers early diagnostics until its shared remote identity is known.
The local terminal and journal aggregate four explicitly labeled local/remote
Elsewindow and Xpra sources through the same severity filter. The remote journal
stores only remote sources. Journal readers and administrators
may see clipboard or application data. Use only non-sensitive debugging data.
Journal permissions, rate limits, storage persistence and retention are host
policy; Elsewindow does not modify them. Bounded native-message framing prevents
log text from injecting journal metadata. Missing journald and later delivery
failures produce a warning while terminal output and the bounded remote relay
remain available; neither failure can own application lifetime.

Remote log subscriptions use only existing-master SSH channels. They need no
privileged journal reader and create no TCP listener or persistent log file.
The ordinary remote owner's raw SSH output also uses bounded, nonblocking
queues. A stalled or closed output consumer cannot hold up group shutdown;
unforwarded bytes are reported while native journal processing continues.
Remote IPC directories are private and ownership-checked; observers remove
only their own socket. The local decoder rejects oversized frames, unexpected
metadata, and records for another session, and keeps remote PIDs as explicit
source metadata rather than local process identity. Terminal rendering retains
the source label and shared session ID on every line and strips control characters.
Persistent log IDs are hashes scoped to the remote machine, account and
application, not executable paths or arguments printed in logs. Concurrent
subscribers have separate sockets; closing one does not remove another. Bounded
nonblocking publication prevents a slow or lost observer from controlling the
application lifetime. New connections do not replay disconnected log history.

By default the remote server and application run in one heartbeat-supervised
process group. Normal detach, cancellation, a signal, lease expiry, or master loss
stops the local client first and then terminates only that recorded group and
its private runtime state. The implementation never searches by executable
name and never modifies an unrelated Xpra session. An application that
deliberately escapes its process group can outlive this boundary.

[Persistent mode](cli.md#persistent) explicitly changes that lifetime boundary.
A transient systemd user service owns the supervisor, Xpra and application cgroup. Disconnect,
cancellation and loss of the SSH master stop only local resources. A new
invocation authenticates once and may resume the recorded service; no running
invocation reconnects automatically. Application exit, including failure,
ends Xpra and the service; `Restart=no` prevents accidental relaunch.

Linger is checked on every persistent invocation. When disabled, a controlling
terminal must supply exact `y` or `yes` before Elsewindow attempts to enable
it for the current remote UID. If necessary, fixed `sudo loginctl` runs in a
PTY on the same owned mux. Sudo reads its password directly; Python neither
captures nor stores it. Refusal or lack of a terminal starts no service.
Without recorded persistent state, unavailable prerequisites select an ordinary
session and warn explicitly that disconnecting stops the application. Existing
records and ownership failures prevent that fallback and cannot start a duplicate.
Linger is an account-wide setting, remains enabled after the application exits,
and keeps other user services alive after logout too. No packages are installed
and there is no unattended-consent option.

The private runtime registry uses bounded reads, atomic records, no-follow
locks and serialized creation. Resumption validates the service's random
ownership token, systemd invocation, PID/start time and Xpra argv metadata.
An unrelated service or incompatible server/clipboard configuration is never
stopped, replaced or silently adopted. Empty lock files are retained to avoid
split-lock races; active records disappear with their owned service. The
selected account is trusted and can modify its own services and registry.

Public diagnostics are bounded and scrub private SSH runtime paths and control
characters.

Explicit application variables travel as inert JSON over the owned SSH channels.
They are applied only when spawning the application, after the owned desktop
helpers start. Session display, runtime-directory, D-Bus, and Elsewindow variables
cannot be overridden. Names, types, entry count, and byte limits are checked on
both peers. Persistent resumes require the recorded variable set without
changing or replacing the running process. Fixed diagnostics do not print the
values; the selected accounts can inspect them in command payloads and private
persistent state.

Local Xpra additions have a separate project venv. Its preparation and startup
do not require a matching filesystem UID or private Unix permission bits, so
shared filesystems with mapped ownership or broader permissions are supported.
Only explicit
[environment setup](cli.md#prepare-xpra) downloads or installs its hash-locked
PyPI packages. Accelerator source builds use a hash-verified archive, separately
locked temporary build tools, and no unpinned build isolation; no builder
packages remain in the activated venv. Inherited pip configuration, alternate
installation destinations, and extra requirement inputs cannot redirect setup;
explicit index/find-links transport settings still require the locked hashes.
System Xpra, GTK and native modules
remain distribution-owned.
Ordinary startup revalidates the current locks, interpreter, installed versions
and file hashes; it does not repair or fall back to a different environment.
Validation reads each installed `RECORD` inventory directly, checking every
hashed entry without repeated filesystem metadata scans or skipping missing
files. Up to four concurrent file reads overlap shared-storage latency; their
results are never cached between invocations. A timeout leaves the environment
intact and is reported separately from stale inputs.
Preparation refuses unowned directories and preserves the previous owned
environment on failure. The Xpra interpreter uses isolated Python imports;
the frozen launcher also restores the host native-library search path before
starting system tools. The selected local user remains trusted to control
their own environment and installed-file metadata.

Default environment paths are scoped to the local machine and UID, including
installed and standalone Xpra environments under shared XDG data directories.
The namespace uses HMAC-SHA-256 with a fixed application-specific key, following
the [systemd machine-ID guidance](https://www.freedesktop.org/software/systemd/man/latest/machine-id.html).
Neither the raw `/etc/machine-id` value nor a substring is placed in directory
names or diagnostics. Missing, invalid, or uninitialized IDs fail closed;
setup does not change the OS identity or fall back to another host's venv.
An explicit Xpra directory override must itself be kept host-local. This is
environment isolation, not protection against another trusted user with write
access to the checkout or against systems cloned with identical machine IDs.

The [diagnostic command](cli.md#informational-commands) performs no SSH
connection. It reports only public versions, SHA-256 digests of packaged
profiles, the Linux platform decision,
and resolved local prerequisite commands. A missing command is emitted with
the stable `elsewindow: missing_dependency:` prefix.

The standalone [package installer](../tools/install_xpra_release.py) is a
separate privileged boundary. It refuses unsupported systems, verifies release
ancestry, checksums, archive structure, DEB metadata, and required runtime
files before mutation, and obtains confirmation from the controlling terminal
when an Xpra inventory exists. It rechecks the inventory immediately before
purge, stages immutable copies in a root-owned directory, and installs through
APT. A failed transaction after purge can leave Xpra absent; review recovery
requirements before running it.

Only amd64 Debian 13 and Ubuntu 26.04 are accepted, and this is checked before
network access. If packages or retained configuration exist, the installer
prints every exact package, version, and dpkg status and accepts only `y` or
`yes` from `/dev/tty`; declining changes nothing and makes no network request.
There is no non-interactive confirmation switch.

A non-root invocation calls fixed `/usr/bin/sudo`, copies the fully validated
archive and DEBs into a root-owned mode-`0700` directory, and verifies those
immutable copies again. It rechecks the confirmed inventory immediately before
purging only that set. Actual DEB members must provide the native libva encoder
and decoder, libyuv converter, GTK OpenGL client, common and server assets, X11
bindings, and required Ubuntu Wayland modules in their declared owners. APT
installs the exact local set plus `libva-drm2`, `python3-opengl`, `python3-venv`,
`dbus-daemon`, and `python3-dbus`, after which
every consumed module is imported. The helper never uses `dpkg -i`, runs
`autoremove`, changes APT sources, or purges an unrelated package.

Project container contexts and fixtures cross stdin/stdout only through the
validated bounded tar helper. Bind mounts, named data volumes, and `podman cp`
are outside the project contract.

Every rootless runtime container uses an explicit bounded subordinate-ID
range. Governance, live client, and live target policies use 2,048 IDs, which
covers the highest consumed application UID 1001. The Ubuntu target moves the
OpenSSH privilege-separation account from legacy `nogroup` GID 65534 to the
dedicated `_ssh` GID before execution, avoiding an unjustified full-range
allocation. Installer and release-image verification use 4,096 IDs. Unbounded
`auto`, `keep-id`, `nomap`, and `--userns=host` are forbidden.

The live target runs systemd as PID 1 and adds only `SETPCAP` to its prior
capability set so system services can reduce their own capability boundaries.
It still excludes `SYS_ADMIN`, privileged mode and host namespaces. Its journal
records SSH authentication, and package-provided Xpra/auxiliary SSH socket
activation is masked so only the explicit test resources are started.
