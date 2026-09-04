<!-- Copyright (c) 2026 kogeler. SPDX-License-Identifier: MIT. -->

# Changelog

All notable changes to Elsewindow are documented here.

## Unreleased

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
