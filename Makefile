SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
.NOTPARALLEL:

SYSTEM_PYTHON ?= python3
PODMAN ?= podman
RUNTIME_VENV := venv-runtime
QUALITY_VENV := venv-quality
TEST_VENV := venv-test
PACKAGE_VENV := venv-package
STANDALONE_VENV := venv-standalone
DOCS_VENV := venv-docs
RUNTIME_PYTHON := $(RUNTIME_VENV)/bin/python
QUALITY_PYTHON := $(QUALITY_VENV)/bin/python
TEST_PYTHON := $(TEST_VENV)/bin/python
PACKAGE_PYTHON := $(PACKAGE_VENV)/bin/python
STANDALONE_PYTHON := $(STANDALONE_VENV)/bin/python
DOCS_PYTHON := $(DOCS_VENV)/bin/python
MKDOCS := $(DOCS_VENV)/bin/mkdocs
RUFF := $(QUALITY_VENV)/bin/ruff
RUNTIME_LOCK := requirements.txt
QUALITY_LOCK := requirements-quality.txt
TEST_LOCK := requirements-test.txt
PACKAGE_LOCK := requirements-package.txt
STANDALONE_LOCK := requirements-standalone.txt
DOCS_LOCK := requirements-docs.txt
STANDALONE_ARCH ?=
RUNTIME_STATE := $(RUNTIME_VENV)/.$(RUNTIME_LOCK)
QUALITY_STATE := $(QUALITY_VENV)/.$(QUALITY_LOCK)
TEST_STATE := $(TEST_VENV)/.$(TEST_LOCK)
PACKAGE_STATE := $(PACKAGE_VENV)/.$(PACKAGE_LOCK)
STANDALONE_STATE := $(STANDALONE_VENV)/.$(STANDALONE_LOCK)
DOCS_STATE := $(DOCS_VENV)/.$(DOCS_LOCK)
DEPENDENCY_WHEEL_DIR := .artifacts/dependency-wheels
SOURCES := elsewindow tests tools doc/site .github/scripts
ARTIFACTS := .artifacts
DEPENDENCY_SNAPSHOT := $(ARTIFACTS)/dependency-snapshot.json
RELEASE_NOTES := $(ARTIFACTS)/release-notes.md
COVERAGE_REPORT := $(ARTIFACTS)/coverage-report.md
COVERAGE_TOTAL := $(ARTIFACTS)/coverage-total.txt
override NORMALIZATION_EPOCH := 315532800
COMPILE := --quiet --strip-extras --allow-unsafe --generate-hashes \
	--no-emit-find-links --rebuild
LOCK_UPGRADE ?=
VERSION_ARGS ?=
ACTIONLINT_IMAGE := docker.io/rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667

GOVERNANCE_CONTEXT := containers/governance/Containerfile \
	containers/toolbox/entrypoint.sh tools/container_payload.py
GOVERNANCE_KEY = $(shell cat $(GOVERNANCE_CONTEXT) | sha256sum | cut -c1-16)
LOCK_TAG := localhost/elsewindow-lock:$(GOVERNANCE_KEY)
CONFINEMENT_TAG := localhost/elsewindow-confinement:$(GOVERNANCE_KEY)
COMPATIBILITY_TAG := localhost/elsewindow-python313:$(GOVERNANCE_KEY)
ACTIONLINT_CONTEXT := containers/actionlint/Containerfile \
	containers/toolbox/entrypoint.sh tools/container_payload.py
ACTIONLINT_KEY = $(shell { cat $(ACTIONLINT_CONTEXT); \
	printf '%s' '$(ACTIONLINT_IMAGE)'; } | sha256sum | cut -c1-16)
ACTIONLINT_TAG := localhost/elsewindow-actionlint:$(ACTIONLINT_KEY)

PROJECT_ARCHIVE = $(SYSTEM_PYTHON) tools/create_live_payload.py
PAYLOAD_MERGE = $(SYSTEM_PYTHON) tools/container_payload.py merge \
	--destination=. --allow-empty
BOX_CONFINE := --rm --interactive --network=none --userns=auto:size=2048 \
	--security-opt=no-new-privileges --cap-drop=ALL --read-only \
	--read-only-tmpfs=false --ipc=private --pid=private --uts=private \
	--cgroupns=private --systemd=false --no-hosts --unsetenv-all --umask=077 \
	--pids-limit=1024 --memory=8g --memory-swap=8g \
	--ulimit=nofile=4096:4096 --log-driver=none --timeout=1800 --pull=never \
	--tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777 \
	--tmpfs=/work:rw,exec,nosuid,nodev,size=2g,mode=1777 \
	--env=HOME=/tmp/home --env=LANG=C.UTF-8 --env=LC_ALL=C.UTF-8 \
	--env=TZ=UTC --env=PATH=/usr/local/bin:/usr/bin:/bin
BOX_ONLINE = $(subst --network=none,--network=slirp4netns,$(BOX_CONFINE))
LOCK_ONLINE = $(subst size=512m,size=4g,$(BOX_ONLINE))

.PHONY: help runtime-venv quality-venv test-venv package-venv standalone-venv \
	docs-venv docs-build docs-audit docs-serve \
	dev-venv dependency-wheels package build standalone smoke-wheel smoke-sdist \
	smoke-standalone smoke reproducibility checksums lock \
	refresh-dependencies freeze-check format format-check lint type-check bandit \
	test syntax shellcheck lock-validate audit audit-raw licenses outdated \
	dependency-snapshot version-check release-notes coverage-report \
	governance-image lock-image compatibility-image validator-image-actionlint \
	validate-actions compatibility-python test-network-block confinement-test \
	doctor image-key image-save-governance image-load-governance \
	xpra-release-status xpra-images xpra-installer-test live-preflight live-test \
	clean-containers check ci clean

help:
	@printf '%s\n' \
		'make runtime-venv       Install the production environment' \
		'make package            Build and validate the wheel and source archive' \
		'make standalone         Build and validate this host architecture binary' \
		'make smoke              Clean-smoke Python artifacts and native binary' \
		'make reproducibility    Compare two clean wheel and sdist builds' \
		'make docs-audit         Render and audit the documentation site' \
		'make docs-serve         Serve the rendered documentation locally' \
		'make lock               Recompile all six hash locks' \
		'make refresh-dependencies Upgrade and recompile all locks' \
		'make check              Run the complete local governance gate' \
		'make ci                 Run host checks, audit, and Python 3.13 compatibility' \
		'make dependency-snapshot Build the exact dependency graph' \
		'make release-notes      Render the current changelog section' \
		'make live-test          Run the automatic Xpra lifecycle matrix'

runtime-venv:
	@if [[ ! -x '$(RUNTIME_PYTHON)' ]] || [[ ! -f '$(RUNTIME_STATE)' ]] || \
		! cmp -s '$(RUNTIME_LOCK)' '$(RUNTIME_STATE)' || \
		! '$(RUNTIME_PYTHON)' -c 'import importlib.metadata, ssh_wrapper; assert importlib.metadata.version("ssh-wrapper") == "0.1.0"' >/dev/null 2>&1; then \
		if [[ -e '$(RUNTIME_VENV)' ]]; then find '$(RUNTIME_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(RUNTIME_VENV)'; \
		$(RUNTIME_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: \
			--requirement '$(RUNTIME_LOCK)'; \
		$(RUNTIME_PYTHON) -m pip check; \
		cp -- '$(RUNTIME_LOCK)' '$(RUNTIME_STATE)'; \
		$(RUNTIME_PYTHON) -m pip uninstall --quiet --yes pip; \
	fi

quality-venv:
	@if [[ ! -x '$(QUALITY_PYTHON)' ]] || [[ ! -f '$(QUALITY_STATE)' ]] || \
		! cmp -s '$(QUALITY_LOCK)' '$(QUALITY_STATE)' || \
		! '$(QUALITY_PYTHON)' -c 'import importlib.metadata, pip, ssh_wrapper; assert importlib.metadata.version("ssh-wrapper") == "0.1.0"' >/dev/null 2>&1; then \
		if [[ -e '$(QUALITY_VENV)' ]]; then find '$(QUALITY_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(QUALITY_VENV)'; \
		$(QUALITY_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: --requirement '$(QUALITY_LOCK)'; \
		cp -- '$(QUALITY_LOCK)' '$(QUALITY_STATE)'; \
	fi
	@$(QUALITY_PYTHON) -m pip check

test-venv:
	@if [[ ! -x '$(TEST_PYTHON)' ]] || [[ ! -f '$(TEST_STATE)' ]] || \
		! cmp -s '$(TEST_LOCK)' '$(TEST_STATE)' || \
		! '$(TEST_PYTHON)' -c 'import importlib.metadata, pip, ssh_wrapper; assert importlib.metadata.version("ssh-wrapper") == "0.1.0"' >/dev/null 2>&1; then \
		if [[ -e '$(TEST_VENV)' ]]; then find '$(TEST_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(TEST_VENV)'; \
		$(TEST_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: --requirement '$(TEST_LOCK)'; \
		cp -- '$(TEST_LOCK)' '$(TEST_STATE)'; \
	fi
	@$(TEST_PYTHON) -m pip check

package-venv:
	@if [[ ! -x '$(PACKAGE_PYTHON)' ]] || [[ ! -f '$(PACKAGE_STATE)' ]] || \
		! cmp -s '$(PACKAGE_LOCK)' '$(PACKAGE_STATE)' || \
		! '$(PACKAGE_PYTHON)' -c 'import build, pip, ssh_wrapper' >/dev/null 2>&1; then \
		if [[ -e '$(PACKAGE_VENV)' ]]; then find '$(PACKAGE_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(PACKAGE_VENV)'; \
		$(PACKAGE_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: --requirement '$(PACKAGE_LOCK)'; \
		cp -- '$(PACKAGE_LOCK)' '$(PACKAGE_STATE)'; \
	fi
	@$(PACKAGE_PYTHON) -m pip check

standalone-venv:
	@if [[ ! -x '$(STANDALONE_PYTHON)' ]] || [[ ! -f '$(STANDALONE_STATE)' ]] || \
		! cmp -s '$(STANDALONE_LOCK)' '$(STANDALONE_STATE)' || \
		! '$(STANDALONE_PYTHON)' -c 'import PyInstaller, pip, ssh_wrapper' >/dev/null 2>&1; then \
		if [[ -e '$(STANDALONE_VENV)' ]]; then find '$(STANDALONE_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(STANDALONE_VENV)'; \
		$(STANDALONE_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: --requirement '$(STANDALONE_LOCK)'; \
		cp -- '$(STANDALONE_LOCK)' '$(STANDALONE_STATE)'; \
	fi
	@$(STANDALONE_PYTHON) -m pip check

docs-venv:
	@if [[ ! -x '$(DOCS_PYTHON)' ]] || [[ ! -f '$(DOCS_STATE)' ]] || \
		! cmp -s '$(DOCS_LOCK)' '$(DOCS_STATE)' || \
		! '$(DOCS_PYTHON)' -c 'import mkdocs, pip, ssh_wrapper' >/dev/null 2>&1; then \
		if [[ -e '$(DOCS_VENV)' ]]; then find '$(DOCS_VENV)' -depth -delete; fi; \
		$(SYSTEM_PYTHON) -m venv '$(DOCS_VENV)'; \
		$(DOCS_PYTHON) -m pip install --quiet --require-hashes \
			--only-binary=:all: --requirement '$(DOCS_LOCK)'; \
		cp -- '$(DOCS_LOCK)' '$(DOCS_STATE)'; \
	fi
	@$(DOCS_PYTHON) -m pip check

dev-venv: quality-venv test-venv package-venv standalone-venv docs-venv

docs-build: docs-venv
	@$(MKDOCS) build --clean --strict

docs-audit: docs-build
	@$(SYSTEM_PYTHON) tools/audit_docs_site.py \
		--site-dir site --site-url 'https://kogeler.github.io/elsewindow/'

docs-serve: docs-venv
	@$(MKDOCS) serve --strict

dependency-wheels: package-venv
	@if [[ -e '$(DEPENDENCY_WHEEL_DIR)' ]]; then \
		find '$(DEPENDENCY_WHEEL_DIR)' -depth -delete; \
	fi
	@mkdir -p '$(DEPENDENCY_WHEEL_DIR)'
	@$(PACKAGE_PYTHON) -m pip download --quiet --require-hashes \
		--only-binary=:all: --no-deps --dest '$(DEPENDENCY_WHEEL_DIR)' \
		--requirement '$(RUNTIME_LOCK)'

package: package-venv
	@$(PACKAGE_PYTHON) tools/build_distributions.py \
		--python '$(PACKAGE_PYTHON)' --output dist --epoch '$(NORMALIZATION_EPOCH)'
	@$(SYSTEM_PYTHON) tools/verify_distribution.py dist \
		--epoch '$(NORMALIZATION_EPOCH)'

build: package

standalone: standalone-venv
	@arch=$$($(STANDALONE_PYTHON) -c \
		'from tools.build_standalone import standalone_architecture; print(standalone_architecture())'); \
	requested='$(STANDALONE_ARCH)'; \
	if [[ -n "$$requested" && "$$requested" != "$$arch" ]]; then \
		printf 'Native architecture %s does not match STANDALONE_ARCH=%s\n' \
			"$$arch" "$$requested" >&2; exit 1; \
	fi; \
	$(STANDALONE_PYTHON) tools/build_standalone.py \
		--python '$(STANDALONE_PYTHON)' --output dist \
		--epoch '$(NORMALIZATION_EPOCH)' --expected-architecture "$$arch"; \
	$(SYSTEM_PYTHON) tools/verify_standalone.py "dist/elsewindow-linux-$$arch" \
		--provenance "$(ARTIFACTS)/standalone-provenance-$$arch.json" \
		--architecture "$$arch" --epoch '$(NORMALIZATION_EPOCH)'

smoke-wheel: package dependency-wheels quality-venv
	@$(SYSTEM_PYTHON) tools/smoke_distribution.py --kind wheel \
		--dist-dir dist --dependency-dist '$(DEPENDENCY_WHEEL_DIR)' \
		--python '$(SYSTEM_PYTHON)' --mypy '$(QUALITY_VENV)/bin/mypy' \
		--build-python '$(PACKAGE_PYTHON)'

smoke-sdist: package dependency-wheels quality-venv
	@$(SYSTEM_PYTHON) tools/smoke_distribution.py --kind sdist \
		--dist-dir dist --dependency-dist '$(DEPENDENCY_WHEEL_DIR)' \
		--python '$(SYSTEM_PYTHON)' --mypy '$(QUALITY_VENV)/bin/mypy' \
		--build-python '$(PACKAGE_PYTHON)'

smoke-standalone: standalone
	@arch=$$($(STANDALONE_PYTHON) -c \
		'from tools.build_standalone import standalone_architecture; print(standalone_architecture())'); \
	$(SYSTEM_PYTHON) tools/smoke_standalone.py "dist/elsewindow-linux-$$arch"

smoke: smoke-wheel smoke-sdist smoke-standalone

reproducibility: package-venv
	@$(SYSTEM_PYTHON) tools/verify_reproducible.py \
		--python '$(PACKAGE_PYTHON)' --epoch '$(NORMALIZATION_EPOCH)'

checksums:
	@$(SYSTEM_PYTHON) tools/checksums.py --directory dist

lock-image:
	@$(PODMAN) image exists '$(LOCK_TAG)' || { \
		$(PROJECT_ARCHIVE) | $(PODMAN) build --quiet --pull=missing \
			--target lock --tag '$(LOCK_TAG)' \
			--file containers/governance/Containerfile - >/dev/null; }

governance-image:
	@$(PODMAN) image exists '$(CONFINEMENT_TAG)' || { \
		$(PROJECT_ARCHIVE) | $(PODMAN) build --quiet --pull=missing \
			--target confinement --tag '$(CONFINEMENT_TAG)' \
			--file containers/governance/Containerfile - >/dev/null; }

compatibility-image:
	@$(PODMAN) image exists '$(COMPATIBILITY_TAG)' || { \
		$(PROJECT_ARCHIVE) | $(PODMAN) build --quiet --pull=missing \
			--target compatibility --tag '$(COMPATIBILITY_TAG)' \
			--file containers/governance/Containerfile - >/dev/null; }

lock: lock-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(LOCK_ONLINE) \
		--env BOX_EXPORT='$(RUNTIME_LOCK) $(QUALITY_LOCK) $(TEST_LOCK) $(PACKAGE_LOCK) $(STANDALONE_LOCK) $(DOCS_LOCK)' \
		--env BOX_EXPORT_ON_SUCCESS=1 '$(LOCK_TAG)' sh -ceu \
		'python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) \
			--output-file=$(RUNTIME_LOCK) pyproject.toml; \
		python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=quality \
			--output-file=$(QUALITY_LOCK) pyproject.toml; \
		python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=test \
			--output-file=$(TEST_LOCK) pyproject.toml; \
		python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=package \
			--output-file=$(PACKAGE_LOCK) pyproject.toml; \
		python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=standalone \
			--output-file=$(STANDALONE_LOCK) pyproject.toml; \
		python -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=docs \
			--output-file=$(DOCS_LOCK) pyproject.toml; \
		chmod 0644 $(RUNTIME_LOCK) $(QUALITY_LOCK) $(TEST_LOCK) $(PACKAGE_LOCK) $(STANDALONE_LOCK) $(DOCS_LOCK)' \
		| $(PAYLOAD_MERGE)
	@chmod 0644 '$(RUNTIME_LOCK)' '$(QUALITY_LOCK)' '$(TEST_LOCK)' \
		'$(PACKAGE_LOCK)' '$(STANDALONE_LOCK)' '$(DOCS_LOCK)'

refresh-dependencies:
	@$(MAKE) lock LOCK_UPGRADE=--upgrade

freeze-check: lock-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(LOCK_ONLINE) '$(LOCK_TAG)' bash -ceu \
		'python -m piptools compile $(COMPILE) --constraint=$(RUNTIME_LOCK) \
			--output-file=/tmp/runtime.txt pyproject.toml; \
		python -m piptools compile $(COMPILE) --extra=quality \
			--constraint=$(QUALITY_LOCK) --output-file=/tmp/quality.txt pyproject.toml; \
		python -m piptools compile $(COMPILE) --extra=test \
			--constraint=$(TEST_LOCK) --output-file=/tmp/test.txt pyproject.toml; \
		python -m piptools compile $(COMPILE) --extra=package \
			--constraint=$(PACKAGE_LOCK) --output-file=/tmp/package.txt pyproject.toml; \
		python -m piptools compile $(COMPILE) --extra=standalone \
			--constraint=$(STANDALONE_LOCK) --output-file=/tmp/standalone.txt pyproject.toml; \
		python -m piptools compile $(COMPILE) --extra=docs \
			--constraint=$(DOCS_LOCK) --output-file=/tmp/docs.txt pyproject.toml; \
		diff -u <(sed "/^[[:space:]]*#/d" $(RUNTIME_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/runtime.txt); \
		diff -u <(sed "/^[[:space:]]*#/d" $(QUALITY_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/quality.txt); \
		diff -u <(sed "/^[[:space:]]*#/d" $(TEST_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/test.txt); \
		diff -u <(sed "/^[[:space:]]*#/d" $(PACKAGE_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/package.txt); \
		diff -u <(sed "/^[[:space:]]*#/d" $(STANDALONE_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/standalone.txt); \
		diff -u <(sed "/^[[:space:]]*#/d" $(DOCS_LOCK)) \
			<(sed "/^[[:space:]]*#/d" /tmp/docs.txt)'

format: quality-venv
	@$(RUFF) check --fix $(SOURCES)
	@$(RUFF) format $(SOURCES)

format-check: quality-venv
	@$(RUFF) format --check $(SOURCES)

lint: quality-venv
	@$(RUFF) check $(SOURCES)

type-check: quality-venv
	@$(QUALITY_PYTHON) -m mypy

bandit: quality-venv
	@$(QUALITY_PYTHON) -m bandit -q -c pyproject.toml -r elsewindow tools doc/site

test: test-venv
	@mkdir -p '$(ARTIFACTS)'; status=0; $(TEST_PYTHON) -m pytest || status=$$?; \
		$(TEST_PYTHON) -m coverage report --format=markdown > '$(COVERAGE_REPORT)'; \
		$(TEST_PYTHON) -m coverage report --format=total > '$(COVERAGE_TOTAL)'; \
		cp -- coverage.xml '$(ARTIFACTS)/coverage.xml'; exit $$status

syntax: test-venv
	@$(TEST_PYTHON) -m compileall -q \
		elsewindow tests tools doc/site .github/scripts
	@bash -n bin/elsewindow containers/toolbox/entrypoint.sh \
		containers/live-target/entrypoint.sh containers/live-target/install-base.sh

shellcheck: governance-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(BOX_CONFINE) '$(CONFINEMENT_TAG)' \
		shellcheck bin/elsewindow containers/toolbox/entrypoint.sh \
		containers/live-target/entrypoint.sh containers/live-target/install-base.sh

lock-validate:
	@$(SYSTEM_PYTHON) .github/scripts/lock_validation.py

audit: quality-venv
	@$(QUALITY_PYTHON) .github/scripts/dependency_audit.py

audit-raw: quality-venv
	@$(QUALITY_PYTHON) -m pip_audit --local --strict

licenses: quality-venv standalone-venv
	@$(QUALITY_PYTHON) -m piplicenses --python '$(STANDALONE_PYTHON)' \
		--format=markdown

outdated: quality-venv
	@$(QUALITY_PYTHON) -m pip list --outdated

dependency-snapshot:
	@mkdir -p '$(ARTIFACTS)'
	@$(SYSTEM_PYTHON) .github/scripts/dependency_snapshot.py \
		--output '$(DEPENDENCY_SNAPSHOT)'

version-check:
	@$(SYSTEM_PYTHON) .github/scripts/version.py check $(VERSION_ARGS)

release-notes:
	@mkdir -p '$(ARTIFACTS)'
	@$(SYSTEM_PYTHON) .github/scripts/version.py notes --output '$(RELEASE_NOTES)'

coverage-report:
	@test -f '$(COVERAGE_TOTAL)' || { \
		printf 'No coverage data; run make test first.\n' >&2; exit 1; }
	@total=$$(cat '$(COVERAGE_TOTAL)'); threshold=$$($(SYSTEM_PYTHON) -c \
		'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["tool"]["coverage"]["report"]["fail_under"])'); \
		printf '### Coverage: %s%% (threshold %s%%)\n\n' "$$total" "$$threshold"; \
		cat '$(COVERAGE_REPORT)'

test-network-block: governance-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(BOX_CONFINE) '$(CONFINEMENT_TAG)' \
		python -c 'import socket; sock=socket.socket(); sock.settimeout(0.2); raise SystemExit(sock.connect_ex(("1.1.1.1",443)) == 0)'

confinement-test: governance-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(BOX_CONFINE) '$(CONFINEMENT_TAG)' sh -ceu \
		'test "$$(id -u)" -ne 0; grep -Eq "^CapEff:[[:space:]]+0+$$" /proc/self/status; \
		grep -Eq "^NoNewPrivs:[[:space:]]+1$$" /proc/self/status; \
		grep -Eq "^Seccomp:[[:space:]]+2$$" /proc/self/status; \
		test ! -e .git; test ! -S /run/podman/podman.sock; \
		test ! -S /var/run/docker.sock; test -z "$${SSH_AUTH_SOCK:-}"; \
		test -z "$${GITHUB_TOKEN:-}"; touch /tmp/write /work/write; \
		touch elsewindow/.container-mutation'
	@test ! -e elsewindow/.container-mutation

compatibility-python: compatibility-image
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(LOCK_ONLINE) '$(COMPATIBILITY_TAG)' sh -ceu \
		'python -m piptools compile $(COMPILE) --extra=test \
			--output-file=/tmp/python313.txt pyproject.toml; \
		python -m venv /tmp/tests; /tmp/tests/bin/python -m pip install --quiet \
			--require-hashes --only-binary=:all: --requirement /tmp/python313.txt; \
		/tmp/tests/bin/python -m pip install --quiet --no-deps --editable .; \
		/tmp/tests/bin/python -m pip check; /tmp/tests/bin/python -m pytest'

validator-image-actionlint:
	@$(PODMAN) image exists '$(ACTIONLINT_TAG)' || { \
		$(PROJECT_ARCHIVE) | $(PODMAN) build --quiet --pull=missing \
			--build-arg ACTIONLINT_IMAGE='$(ACTIONLINT_IMAGE)' \
			--tag '$(ACTIONLINT_TAG)' --file containers/actionlint/Containerfile - \
			>/dev/null; }

validate-actions: validator-image-actionlint
	@$(PROJECT_ARCHIVE) | $(PODMAN) run $(BOX_CONFINE) '$(ACTIONLINT_TAG)' \
		actionlint -no-color -config-file .github/actionlint.yaml \
		.github/workflows/*.yml

doctor:
	@for required in sha256sum '$(SYSTEM_PYTHON)' '$(PODMAN)'; do \
		type -P "$$required" >/dev/null || { \
			printf 'required command not found: %s\n' "$$required" >&2; exit 1; }; \
	done
	@test "$$($(PODMAN) info --format '{{.Host.Security.Rootless}}')" = true

image-key:
	@printf '%s\n' 'governance=$(GOVERNANCE_KEY)' 'actionlint=$(ACTIONLINT_KEY)'

image-save-governance: governance-image
	@mkdir -p '$(ARTIFACTS)/images'; $(PODMAN) save --quiet --format oci-archive \
		'$(CONFINEMENT_TAG)' > '$(ARTIFACTS)/images/governance.tar'

image-load-governance:
	@$(PODMAN) load --quiet < '$(ARTIFACTS)/images/governance.tar' >/dev/null
	@$(PODMAN) image exists '$(CONFINEMENT_TAG)'

xpra-release-status:
	@$(SYSTEM_PYTHON) tools/install_xpra_release.py resolve

xpra-images:
	@PODMAN='$(PODMAN)' $(SYSTEM_PYTHON) tools/prepare_xpra_images.py prepare

xpra-installer-test:
	@$(SYSTEM_PYTHON) tests/xpra_installer_acceptance.py

LIVE_HARNESS := tests/live_harness.py
LIVE_WHEEL := dist/elsewindow-$(shell cat .version)-py3-none-any.whl
LIVE_CLIENT_CONFINE = --pull=never --userns=auto:size=2048 \
	--security-opt=no-new-privileges --cap-drop=ALL --read-only \
	--read-only-tmpfs=false --ipc=private --pid=private --uts=private \
	--cgroupns=private --systemd=false --no-hosts --unsetenv-all --umask=077 \
	--pids-limit=1024 --memory=8g --memory-swap=8g \
	--ulimit=nofile=4096:4096 --log-driver=none --timeout=1800 \
	--tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777 \
	--tmpfs=/work:rw,exec,nosuid,nodev,size=2g,mode=1777 \
	--mount=type=tmpfs,destination=/home/box,tmpfs-size=16777216,tmpfs-mode=0700,chown=true \
	--env=HOME=/home/box --env=LANG=C.UTF-8 --env=LC_ALL=C.UTF-8 \
	--env=TZ=UTC --env=PATH=/usr/local/bin:/usr/bin:/bin
LIVE_TARGET_CONFINE = --pull=never --userns=auto:size=2048 \
	--cap-drop=ALL --cap-add=AUDIT_WRITE \
	--cap-add=CHOWN --cap-add=DAC_OVERRIDE --cap-add=FOWNER --cap-add=KILL \
	--cap-add=NET_ADMIN --cap-add=NET_BIND_SERVICE --cap-add=SETGID \
	--cap-add=SETUID --cap-add=SYS_CHROOT --ipc=private --pid=private \
	--uts=private --cgroupns=private --systemd=false --pids-limit=512 \
	--memory=1g --memory-swap=1g --log-driver=k8s-file \
	--tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777

live-preflight: runtime-venv xpra-images
	@target_image=$$($(SYSTEM_PYTHON) tools/prepare_xpra_images.py image --role target); \
	client_image=$$($(SYSTEM_PYTHON) tools/prepare_xpra_images.py image --role client); \
	PYTHONPATH='$(CURDIR)' PODMAN='$(PODMAN)' $(RUNTIME_PYTHON) $(LIVE_HARNESS) \
		--target-image "$$target_image" --client-image "$$client_image" --preflight-only

live-test: runtime-venv package xpra-images
	@target_image=$$($(SYSTEM_PYTHON) tools/prepare_xpra_images.py image --role target); \
	client_image=$$($(SYSTEM_PYTHON) tools/prepare_xpra_images.py image --role client); \
	$(SYSTEM_PYTHON) tools/create_live_payload.py \
		--packaged-wheel '$(LIVE_WHEEL)' | PODMAN='$(PODMAN)' \
	ELSEWINDOW_LIVE_CLIENT_CONFINE='$(LIVE_CLIENT_CONFINE)' \
	ELSEWINDOW_LIVE_TARGET_CONFINE='$(LIVE_TARGET_CONFINE)' \
	PYTHONPATH='$(CURDIR)' $(RUNTIME_PYTHON) $(LIVE_HARNESS) \
		--target-image "$$target_image" \
		--client-image "$$client_image"

clean-containers:
	@containers=$$($(PODMAN) ps --all --quiet --filter \
		'label=io.elsewindow.live.owner' 2>/dev/null); \
	if [[ -n "$$containers" ]]; then $(PODMAN) rm --force --time 5 $$containers >/dev/null; fi
	@networks=$$($(PODMAN) network ls --quiet --filter \
		'label=io.elsewindow.live.owner' 2>/dev/null); \
	if [[ -n "$$networks" ]]; then $(PODMAN) network rm --force $$networks >/dev/null; fi

check: format-check lint type-check bandit test package reproducibility docs-audit syntax \
	shellcheck lock-validate test-network-block confinement-test \
	dependency-snapshot version-check validate-actions

ci: check freeze-check audit compatibility-python

clean:
	@find elsewindow tests tools doc/site .github/scripts -type f \
		-path '*/__pycache__/*' -delete
	@find elsewindow tests tools doc/site .github/scripts -depth -type d \
		-name __pycache__ -empty -delete
	@for path in '$(RUNTIME_VENV)' '$(QUALITY_VENV)' '$(TEST_VENV)' \
		'$(PACKAGE_VENV)' '$(STANDALONE_VENV)' '$(DOCS_VENV)' .pytest_cache \
		.ruff_cache .mypy_cache .coverage coverage.xml '$(ARTIFACTS)' \
		__pycache__ build dist site elsewindow.egg-info; do \
		if [[ -e "$$path" ]]; then find "$$path" -depth -delete; fi; \
	done
