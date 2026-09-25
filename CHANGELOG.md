<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Changelog

All notable changes to Elsewindow are documented here.

## Unreleased

## [0.2.1] - 2026-09-08

### Added

- Include the standard desktop portal and GTK backend in explicit system-package
  preparation and the release-backed disposable images. Startup never installs
  system packages.
- Own a private foreground desktop portal, GTK backend and transient permission
  store for remote file dialogs and portal notifications. OpenURI and unrelated
  desktop portal methods remain disabled.
- Prevent repeated child-start requests from launching a second application or
  competing portal helpers within the same owned session.
- Add repeatable `--env NAME=VALUE` and `--env NAME` application overrides for
  ordinary and persistent sessions. Preserve literal and empty values, isolate
  overrides from session helpers, and require matching values on persistent
  reconnects.
- Document that Zed's default `notify_when_agent_waiting` opens no agent
  notification window in any Wayland session, including Elsewindow's, and that
  `all_screens` delivers them as forwarded windows.
- Cover application-drawn agent notification windows in the live test with a
  lightweight probe of Zed's Wayland flow, the operator's persistent H.264
  `gigabit_lan` command line, and a real xfwm4 desktop across a persistent
  reconnect.
- Resume the ordered live matrix at a fixed failure with
  `make live-test LIVE_FROM=CASE`, and run selected tests without the coverage
  gate with `make test-focused TESTS=...`. Only a complete live run validates a
  change.

### Changed

- Update the published SSH runtime dependency through its single requirements
  input. Derive environment, lock, clean-install, standalone and live identity
  checks from that input instead of maintaining duplicate version constants.
- Missing journald no longer prevents ordinary or persistent application startup;
  warn with setup guidance and retain terminal output and the remote log relay.
- Check optional notification and GPU prerequisites on both hosts. Missing
  support disables delivery or selects RGB for this connection with explicit
  package guidance; absent portal services never prevent application startup.
- Unavailable persistence or declined linger falls back to an ordinary session
  only when no persistent state is recorded, with a warning about disconnect
  cleanup. Never replace or duplicate an existing persistent application.
- Synchronized the maintained fork's client wheel default and pointer diagnostics
  without duplicating the mirrored wheel option in the session policy.

### Fixed

- Prevent an open but unread SSH output pipe from blocking ordinary remote
  shutdown. Bound raw-output queues, retain native journal processing, and report
  dropped bytes without postponing owned application cleanup.
- Wait for the remote dialog's actual focus before sending its dismissal key
  in the live GUI test. Repeat abrupt master loss and retain bounded process,
  owned-path and shutdown-journal evidence on cleanup failure.
- Allow local Xpra environment preparation, reuse, and startup on shared
  filesystems with mapped ownership or broader Unix permissions.
- Use the same resolved Xpra executable for preparation, diagnosis, and session
  startup when PATH contains symbolic links. Allow longer installed-file checks
  on shared storage and report timeouts without treating them as stale
  environments or reinstalling dependencies.
- Read installed file inventories directly with bounded concurrent reads during
  Xpra validation to avoid repeated filesystem metadata scans and detect missing
  package files.
- Consume the canonical Xpra client modal-window default from the mirrored YAML
  instead of duplicating it in Python.
- Mask the current packaged Xpra socket and service in the disposable target so
  its only TCP listener is SSH.

## [0.2.0] - 2026-09-05

### Added

- Enabled remote application notifications by default, with a private owned
  foreground D-Bus session per application session and no Xpra D-Bus control.
- Added a separate system-Python Xpra environment with hash-locked matching
  PyOpenGL and accelerator additions. `make runtime-venv` prepares both local
  environments; installed and standalone commands provide `--prepare-xpra`.
  All local Xpra commands use the prepared interpreter, while ordinary startup
  validates current inputs and installed bytes without installing anything.
- Bundled Xpra setup resources in all artifacts and shared clean setup,
  stale-environment rejection, and repair smoke across wheel, sdist, and
  standalone routes. Extended the existing live topology to exercise the
  prepared client and its public zero-copy capability.
- Support accelerator source builds when a wheel is unavailable, including
  Linux arm64, using a hash-verified archive and separately locked temporary
  build tools. Exercise offline native compilation in every artifact smoke.
- Added synchronized `--log-level` control for Elsewindow and Xpra logs on both
  hosts, with `warning` by default, host-local journald records in ordinary and
  persistent sessions, and matching local stdout/stderr output.
- Added targeted diagnostics with `--log-level=debug-clipboard` and general
  diagnostics with `--log-level=debug`. Both modes warn that system journals
  may contain sensitive data.
- Preserved local Xpra probe diagnostics in the journal, including failures
  before SSH authentication, and added side/session/source-process metadata.
- Extended the existing live topology to verify real journals on both hosts,
  identical local terminal output, and unchanged ordinary/persistent cleanup.
- Aggregate four clearly labeled local/remote Elsewindow and Xpra sources in
  the local terminal and journal. Forward new remote records through owned
  SSH mux channels in both lifecycle modes; keep the remote journal limited
  to remote sources, without granting journal-reading privileges or reconnecting.
- Include one shared session ID in every log message and native journal record
  on both hosts. Keep ordinary invocations distinct and persistent IDs stable
  across reconnects, namespaced by remote machine and account; isolate concurrent
  log subscribers without changing application identity or ownership.
- Added `--persistent` sessions backed by owned transient user systemd
  services. Repeating the same remote executable invocation path and exact
  arguments resumes the application after client or SSH loss; exiting the
  foreground application with any status ends the session. Ordinary
  heartbeat-supervised sessions remain the default.
- Added per-invocation linger checks and interactive consent to enable it for
  the remote account, using the same owned SSH connection for any required
  sudo prompt. Persistent sessions never reconnect or restart automatically.
- Extended the existing release-backed live topology with real systemd,
  linger refusal/consent, detach and client/SSH-loss recovery, cancellation,
  repeated manual reconnects, and cleanup after successful or failed
  application exit.

### Changed

- Isolated all checkout Python environments by an application-specific hash of
  the OS machine ID and local UID, shared by Make and the repository launcher.
  Installed and standalone Xpra defaults use the same host isolation for shared
  data directories. Leave legacy environments and other machines' venvs untouched.
- Persistent resumption requires the original logging policy.
- Consolidated all public CLI options, defaults, profiles, and restrictions
  into one linked reference, with parser-backed documentation checks.
- Synchronized the maintained-fork clipboard YAML blocks and assembled the
  same reviewed policy for both peers without duplicating option values or
  applying the fork's clipboard-test-only X11 overrides.
- Included the persistent-session helper in wheel, source, and standalone
  distributions, verified its packaged bytes, and exposed its source digest
  through `--diagnose`.

### Fixed

- Restored ordinary GUI behavior suppressed by the minimal Xpra base: custom
  cursors, mouse-wheel forwarding, keyboard-state synchronization, modal windows,
  detected DPI, and client scaling initially at 1:1, without new CLI switches.
- Routed `SIGHUP` through normal session cancellation and terminated owned
  local SSH probe processes when their requests are cancelled.

## [0.1.1] - 2026-09-04

### Changed

- Enabled bidirectional Xpra clipboard synchronization by default, with
  explicit `off`, `to-server`, and `both` policies applied to both peers.
- Synchronized the mirrored Xpra keyboard diagnostics with the current
  maintained-fork configuration.
- Moved all exact direct Python versions to six native
  `requirements*.in`/`requirements*.txt` pip-compile pairs so Dependabot can
  update each complete graph instead of treating `pyproject.toml` as a plain
  requirements manifest.
- Aligned both local lock resolver stages with Dependabot's pip and pip-tools
  pair, and made published runtime metadata derive from `requirements.in`
  without a second version authority.
- Made an exact existing release suppress the reusable release CI and both
  publication jobs, while ordinary CI accepts maintenance at an already
  published version and authenticates installer acceptance API requests.
- Added trusted-base PR metadata automation that mirrors a populated
  `Unreleased` section without requiring a version change, preserving manual
  PR text while release-worthy changes accumulate across merges.

## [0.1.0] - 2026-08-30

### Added

- Established Elsewindow as an independent Linux project with one public
  distribution, import package, command, and repository identity.
- Preserved the one-authentication OpenSSH lifecycle, mux-only Xpra channels,
  heartbeat-supervised remote process group, and selective cleanup behavior.
- Shipped reviewed Xpra command and network profiles as package data and kept
  release-backed package installation and container acceptance tooling.
- Added CPython 3.13 and 3.14 governance, deterministic Python distributions,
  Linux standalone artifacts, and fail-closed publication policy.
