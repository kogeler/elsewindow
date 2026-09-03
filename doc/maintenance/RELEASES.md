# Release Contract

`.version` is the only human-maintained stable semantic version. Set it once,
add one dated `## [X.Y.Z] - YYYY-MM-DD` changelog section, and run
`make version-check`. Dynamic package metadata, `elsewindow.__version__`, the
`vX.Y.Z` tag, release name, and generated notes all resolve from that value.

The release inventory is exact:

| Destination | Artifacts |
|---|---|
| PyPI | wheel and sdist only |
| GitHub Release | the same wheel and sdist bytes, both Linux executables, `SHA256SUMS.txt` |

Reusable CI builds every byte once. Publication jobs only download those
verified artifacts; they never rebuild. Equivalent clean source trees must
produce byte-identical normalized wheel and sdist bytes. Each native
PyInstaller build instead carries checked provenance because the tool does not
promise a portable byte-reproducible one-file executable.

On a direct `main` push, the state job first inspects PyPI and GitHub
independently. Existing project, version, tag, tagged source commit, notes,
filename, type, size, SHA-256, yanked state, executable inventory, and checksum
bytes must all match. A complete published match is a no-op even when `main`
has advanced: the reusable release CI and both publication jobs are skipped,
and metadata is checked against the immutable tagged source instead of the new
head. An exact draft may resume by uploading only missing artifacts.
Unexpected, duplicate, incomplete published, moved-tag, or byte-conflicting
state fails without deletion or overwrite.

PyPI publication uses the pinned official action, one GitHub Environment named
`pypi`, and job-scoped `id-token: write`. No API token or password is accepted.
GitHub publication starts only after PyPI succeeds or is already an exact
match. It creates or recovers an exact draft, verifies every existing and new
asset, then publishes the immutable `vX.Y.Z` release.

Before the first push, the operator must:

1. run every local gate against the reviewed working tree, then create and
   review the first commit without treating commit identity as validation
   evidence;
2. create the GitHub `pypi` Environment;
3. configure the pending PyPI Trusted Publisher for repository
   `kogeler/elsewindow`, workflow `release.yml`, environment `pypi`;
4. enable GitHub Pages with GitHub Actions as the source and protect the
   `github-pages` Environment according to repository policy;
5. enable private vulnerability reporting and configure branch/ruleset
   protections, CODEOWNERS review, conversation resolution, and the stable
   checks exposed by hosted CI;
6. push without amending while publication runs and verify hosted hashes and
   the canonical documentation site.

Local gates always read the current filesystem bytes, including maintained
changes that are not committed. Distribution normalization uses a fixed
ZIP-compatible epoch and never reads a commit timestamp. Artifact and source
digests describe produced release evidence; they are not cached gate results
and cannot authorize skipping a later validation run.

Pages publication is owned only by the narrow workflow described in [the CI
contract](CI.md). Never create a tag, release, upload, publisher, or repository
setting as part of a local validation run.
