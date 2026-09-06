<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Changelog

All notable changes to Elsewindow are documented here.

## Unreleased

## [0.2.0] - 2026-09-05

### Added

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
