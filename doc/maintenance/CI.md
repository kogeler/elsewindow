# CI Contract

The workflows in [`.github/workflows/`](../../.github/workflows/) are active
from this repository root. All external actions use full immutable commit
SHAs, checkout credentials are disabled, and the workflow default permission
is `contents: read`.

[`ci.yml`](../../.github/workflows/ci.yml) owns these independent gates:

- quality, strict typing, policy, lock, documentation-link, deterministic
  package, actionlint, and confinement checks;
- the full unit/policy suite on CPython 3.13 and 3.14;
- normalized wheel/sdist build, two-tree reproducibility, clean installation,
  resources, console, and installed typing;
- native one-file builds and smoke on the official `ubuntu-26.04` amd64 and
  `ubuntu-26.04-arm` arm64 runners;
- exact-lock audit and same-repository pull-request dependency review;
- Python and Actions CodeQL;
- exact version validation, progression for a new or unpublished version, and
  maintenance changes that retain an already published current version;
- Debian 13 and Ubuntu 26.04 installer acceptance using only the newest
  currently published maintained-fork package release and the job-scoped
  read token for rate-isolated API access inside its disposable guests;
- one release-backed SSH/Xpra lifecycle gate using a clean-installed wheel.

The two architecture jobs build their executable natively; no cross-labeled
or foreign-platform artifact is accepted. Their labels come from the public
[GitHub runner-images inventory](https://github.com/actions/runner-images#available-images).

[`dependency-submission.yml`](../../.github/workflows/dependency-submission.yml)
runs only on trusted direct `main` changes. Its one job receives
`contents: write` and submits exactly the six validated lock manifests.
[`release.yml`](../../.github/workflows/release.yml) grants OIDC only to the
PyPI job and `contents: write` only to the GitHub publication job. Its reusable
CI gate runs only when exact publication-state inspection finds work for the
version currently stored in `.version`; an already complete release skips the
gate regardless of which files changed in the triggering push.

[`pages.yml`](../../.github/workflows/pages.yml) renders the current
repository documentation on pull requests and direct `main` pushes. Its build
job has read-only permissions and runs the same strict offline audit as
`make check`. Only the direct-main deploy job receives `pages: write` and
`id-token: write`; pull requests never upload or deploy a Pages artifact.

Run `make validate-actions` after workflow changes. This locally checks syntax
and pinned actions in a checksum-bound actionlint container. Structural policy
tests validate triggers, permissions, runners, artifact routing, CodeQL, and
publication boundaries. The documentation audit validates generated routes,
links, anchors, canonical URLs, sitemap forms, `robots.txt`, and `llms.txt`.
Local success does not claim that hosted jobs ran.
