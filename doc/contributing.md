# Contributing

Work from this project directory. Keep changes limited to Elsewindow and
its explicit published dependency contract.

Before submitting a change:

```bash
make format
make check
```

Packaging changes also require `make package reproducibility smoke`.
Documentation changes require `make docs-audit`. Workflow changes require
`make validate-actions`. Lifecycle or image changes require
`make live-preflight` and `make live-test`; installer changes require `make
xpra-installer-test`.

Changes to package installation or image preparation also require the relevant
container acceptance target from [Development](development.md). Changes to
Xpra arguments require a fresh comparison with both canonical fork YAML files
and tests of the complete assembled argv.

Direct Python pins belong in the matching `requirements*.in` file. Each tool
input extends `requirements.in`; run `make lock` and review all six generated
`.txt` locks after changing a direct dependency. Do not hand-edit a lock.

## Changelog and release intent

Add release-worthy changes to the existing `## Unreleased` section in
[`CHANGELOG.md`](../CHANGELOG.md). Keep `.version` unchanged in an ordinary
pull request: multiple changes and multiple merged pull requests may
accumulate under `Unreleased` before a maintainer deliberately prepares a
release.

Changing `CHANGELOG.md` triggers the dedicated PR metadata workflow. It copies
the newest populated level-two section—normally `## Unreleased`, even when
`.version` did not change—into a marker-delimited block in the pull-request
body. Manual text outside that block is preserved. Do not edit or duplicate
the marker lines; update the changelog or write outside the managed block.

Only a deliberate release change advances `.version` and moves the accumulated
notes into one matching dated `## [X.Y.Z] - YYYY-MM-DD` section. Follow the
[release contract](maintenance/RELEASES.md) for that operation.

Review for these properties:

- no secondary authentication or reconnect path;
- no forwarding or Xpra TCP listener;
- cleanup targets only recorded owned resources;
- errors remain bounded and path-free;
- profile values have one YAML authority;
- package and image operations fail closed before mutation or publication;
- internal links are relative and repository-owned text is English;
- no source, test, documentation, or tool from another application project is
  present.

Do not commit generated caches, environments, image descriptors, package
archives, credentials, SSH material, or host paths.
