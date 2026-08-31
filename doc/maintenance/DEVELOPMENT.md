# Development Contract

Use the repository root and run focused tests with each change. The normal
local sequence is:

```bash
make dev-venv
make format
make check
```

`make check` covers formatting, lint, strict package typing, Bandit, coverage,
normalized package build, two-clean-tree wheel/sdist reproducibility, strict
documentation rendering and offline site audit, syntax, ShellCheck,
lock/version/snapshot policy, actionlint, network denial, and container
confinement. `make ci` adds online lock re-resolution and advisory audit plus
the full Python 3.13 compatibility container.

Parallel coverage data is confined to ignored `.artifacts/` state so xdist
workers cannot race repository-boundary inventory checks. `make clean` removes
that state together with the generated reports.

Repository-boundary tests enumerate the payload directly and do not require a
Git executable or `.git` metadata. This is the same boundary used by the
minimal CPython compatibility container. The bounded container archive uses
that filesystem inventory too, so tracked, modified, and untracked maintained
files are treated identically. Generated and agent-owned roots have an
explicit project-owned exclusion list; Git ignore or index state never
selects gate inputs.

Artifact-specific gates are:

```bash
make package
make standalone
make smoke
make reproducibility
```

`make package` produces and verifies exactly one wheel and one sdist. Wheel
and sdist smoke occurs outside the source tree, installs the reviewed runtime
wheel without an index, exercises module and console routes, verifies bundled
YAML and `py.typed`, and type-checks a consumer against the installed package.

`make standalone` builds only the native host architecture. The verifier
checks its exact name, ELF machine, executable mode, source/lock provenance,
and absence of private paths. `make smoke-standalone` exercises help, version,
resources, dependency identity, invalid configuration, and missing OpenSSH/
Xpra diagnostics from an unrelated directory. PyInstaller output is not
claimed byte-identical; its non-release provenance JSON records the exact
artifact hash, size, build tools, epoch, source digest, and lock digest.
Archive timestamps use the fixed `315532800` normalization epoch. A commit
timestamp, repository ref, or local artifact digest is never a prerequisite
for running or accepting the current working tree.

The release-backed gates remain separate because they use real Podman images:

```bash
make live-preflight
make live-test
make xpra-installer-test
```

The live payload excludes the package source and clean-installs the verified
wheel into the client. The driver proves the installed import and metadata
before the detach and master-loss cases. Internal codec, renderer, application,
and downstream-patch acceptance belongs to the maintained Xpra fork.
