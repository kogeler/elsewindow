# Dependency Contract

[`pyproject.toml`](../../pyproject.toml) owns every exact direct requirement.
Generated pip-compile locks separate six audiences:

| Audience | Lock | Purpose |
|---|---|---|
| runtime | [`requirements.txt`](../../requirements.txt) | published `ssh-wrapper` runtime |
| quality | [`requirements-quality.txt`](../../requirements-quality.txt) | Ruff, mypy, Bandit, audit, licenses |
| test | [`requirements-test.txt`](../../requirements-test.txt) | pytest and coverage |
| package | [`requirements-package.txt`](../../requirements-package.txt) | wheel/sdist construction |
| standalone | [`requirements-standalone.txt`](../../requirements-standalone.txt) | native PyInstaller build |
| docs | [`requirements-docs.txt`](../../requirements-docs.txt) | strict MkDocs rendering |

`ssh-wrapper==0.1.0` is the sole runtime dependency. Every audience that needs
it resolves the reviewed PyPI wheel through generated hashes. This repository
does not vendor it, build it from another checkout, or add an import path to an
uninstalled source tree.

Run `make lock` only after an intentional direct dependency change and
`make refresh-dependencies` for a reviewed whole-graph upgrade. Both use the
online resolver container. Never edit a lock manually. Environment targets
install with `--require-hashes`, binary-only policy, and `pip check`.

`make freeze-check` is an explicit online gate that recompiles every audience
against its current constraints.
`make lock-validate` checks exact ownership, pins, hashes, and wrapper
provenance. `make dependency-snapshot` produces the six GitHub dependency
manifests deterministically.

Dependabot groups all ordinary Python version updates into one pull request and
all GitHub Actions updates into one other pull request, so one scheduled run
opens at most one update per ecosystem. The coordinated `ssh-wrapper` runtime
contract is excluded from automatic version changes. Every accepted Python
update changes its direct pin and generated affected locks together so shared
transitive dependencies stay coherent.

`make audit` checks every lock and accepts only exact findings in
[`dependency-audit-exceptions.json`](../../.github/dependency-audit-exceptions.json);
an unused exception fails as stale. `make licenses` inventories the complete
standalone build environment, a conservative superset of bundled Python
packages; `make outdated` is an explicit online review aid. Maintainer locks
are not a public installation interface: users install the published
distribution normally.
