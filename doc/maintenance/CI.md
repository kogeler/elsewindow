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
  ordinary maintenance changes that retain an already published current
  version while `Unreleased` notes accumulate;
- Debian 13 and Ubuntu 26.04 installer acceptance using only the
  maintained-fork package release pinned in `XPRA_TEST_RELEASE` and the job-scoped
  read token for rate-isolated API access inside its disposable guests;
- one release-backed SSH/Xpra lifecycle gate using a clean-installed wheel.

The two architecture jobs build their executable natively; no cross-labeled
or foreign-platform artifact is accepted. Their labels come from the public
[GitHub runner-images inventory](https://github.com/actions/runner-images#available-images).

[`dependency-submission.yml`](../../.github/workflows/dependency-submission.yml)
runs only on trusted direct `main` changes. Its one job receives
`contents: write` and submits exactly the eight validated lock manifests.
Because pull requests never run this job, a unit test keeps its accepted
manifest list equal to the snapshot generator's output.
[`release.yml`](../../.github/workflows/release.yml) grants OIDC only to the
PyPI job and `contents: write` only to the GitHub publication job. Its reusable
CI gate runs only when exact publication-state inspection finds work for the
version currently stored in `.version`; an already complete release skips the
gate regardless of which files changed in the triggering push. The workflow
therefore performs the minimal external-state inspection on every direct
`main` push instead of treating a `.version` diff, an ordinary merge, or a
populated `Unreleased` section as release intent.

[`pr-body.yml`](../../.github/workflows/pr-body.yml) runs when a pull request
changes `CHANGELOG.md`. Its `pull_request_target` boundary checks out only
trusted default-branch code and reads the exact head changelog through the
GitHub API as bounded inert data. A populated `## Unreleased` section is copied
into one marker-delimited PR-body block without requiring a new version.
Manual text outside the block is preserved, and a body changed concurrently is
never overwritten. This is the only workflow granted `pull-requests: write`;
it never checks out or executes pull-request head code.

[`pages.yml`](../../.github/workflows/pages.yml) renders the current
repository documentation on every pull request and on direct `main` pushes
that change documentation inputs. Its build
job has read-only permissions and runs the same strict offline audit as
`make check`. Only the direct-main deploy job receives `pages: write` and
`id-token: write`; pull requests never upload or deploy a Pages artifact.

### Required status checks

The `main` ruleset requires the stable job names of `ci.yml` and `pages.yml`
(including `Render and audit site`) plus code scanning. GitHub waits
indefinitely for a required check that a pull request never starts, so every
`pull_request` trigger runs unconditionally: no `paths`, `paths-ignore`,
`branches`, or `branches-ignore` filters. A unit test enforces this for every
workflow. When a job must stay cheap for unrelated changes, let it start and
decide inside its steps instead of filtering the trigger. Renaming a required
job also requires updating the ruleset in the same change. A pull request opened
before such a fix gets the check after its head is updated, for example with
`@dependabot rebase`.

Run `make validate-actions` after workflow changes. This locally checks syntax
and pinned actions in a checksum-bound actionlint container. Structural policy
tests validate triggers, permissions, runners, artifact routing, CodeQL, and
publication boundaries. The documentation audit validates generated routes,
links, anchors, canonical URLs, sitemap forms, `robots.txt`, and `llms.txt`.
Local success does not claim that hosted jobs ran.
