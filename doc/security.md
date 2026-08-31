# Security Model

The user directly selects the SSH authority and remote application argv. The
selected remote account and application are trusted to access everything that
account normally can. Standard OpenSSH configuration, host keys, agents,
ProxyJump configuration, and hardware-token interaction remain trusted input.

One invocation owns one foreground OpenSSH ControlMaster. Every secondary
operation requires its mux socket and is configured so it cannot authenticate
or reconnect independently. OpenSSH forwarding, agent sharing, X11 forwarding,
and configured local or remote commands are disabled. Xpra TCP, HTML, SSH
upgrade, clipboard, audio, webcam, printing, file transfer, URL opening,
notifications, and automatic reconnection are disabled.

The remote server and application run in one heartbeat-supervised process
group. Normal detach, cancellation, a signal, lease expiry, or master loss
stops the local client first and then terminates only that recorded group and
its private runtime state. The implementation never searches by executable
name and never modifies an unrelated Xpra session. An application that
deliberately escapes its process group can outlive this boundary.

Public diagnostics are bounded and scrub private SSH runtime paths and control
characters.

`elsewindow --diagnose` performs no SSH connection. It reports only public
versions, SHA-256 digests of packaged profiles, the Linux platform decision,
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
installs the exact local set plus `libva-drm2` and `python3-opengl`, after which
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
