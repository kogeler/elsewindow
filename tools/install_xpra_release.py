#!/usr/bin/env python3

# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Resolve and install the newest canonical Xpra fork package release."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Final, TextIO

REPOSITORY: Final = "kogeler/xpra"
DEVELOP_BRANCH: Final = "develop"
RELEASES_API: Final = f"https://api.github.com/repos/{REPOSITORY}/releases"
COMPARE_API: Final = f"https://api.github.com/repos/{REPOSITORY}/compare"
WORKFLOW: Final = ".github/workflows/deb-packages.yml"
TRANSACTION_OWNER: Final = "xpra-deb-packages"
TRANSACTION_PREFIX: Final = "<!-- xpra-deb-transaction:"
TRANSACTION_PATTERN: Final = re.compile(
    r"<!-- xpra-deb-transaction:(\{.*?\}) -->", re.DOTALL
)
ASSET_NAMES: Final = (
    "xpra-debian-13-amd64-debs.tar",
    "xpra-ubuntu-26.04-amd64-debs.tar",
)
ASSET_FOR_DISTRO: Final = {
    "debian-13": ASSET_NAMES[0],
    "ubuntu-26.04": ASSET_NAMES[1],
}
ALLOWED_DOWNLOAD_HOSTS: Final = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
MAX_RELEASE_PAGES: Final = 100
RELEASES_PER_PAGE: Final = 100
MAX_JSON_BYTES: Final = 8 * 1024 * 1024
MAX_ASSET_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS: Final = 256
MAX_MANIFEST_BYTES: Final = 2 * 1024 * 1024
MAX_PACKAGE_BYTES: Final = 512 * 1024 * 1024
HTTP_TIMEOUT: Final = 60.0
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_PATTERN: Final = re.compile(r"^xpra(?:-[a-z0-9][a-z0-9+.-]*)?$")
PACKAGE_FILE_PATTERN: Final = re.compile(
    r"^(xpra(?:-[a-z0-9][a-z0-9+.-]*)?)_([^/]+)_amd64\.deb$"
)
TRANSACTION_KEYS: Final = frozenset(
    {
        "assets",
        "attempt",
        "commit",
        "owner",
        "repository",
        "run_id",
        "schema",
        "version",
        "workflow",
    }
)
TRANSACTION_ASSET_KEYS: Final = frozenset({"digest", "size"})
MANIFEST_KEYS: Final = frozenset(
    {
        "architecture",
        "base_image_id",
        "base_version",
        "builder_image_id",
        "builder_image_input_sha256",
        "checkout_commit",
        "debian_version",
        "distro",
        "packages",
        "revision",
        "revision_first_parent_count",
        "schema",
        "selection",
        "selection_cache_sha256",
        "selection_resolution_sha256",
        "selection_sha256",
        "source_commit",
        "source_ref",
        "source_ref_commit",
        "workflow_sha256",
    }
)
PACKAGE_KEYS: Final = frozenset(
    {"architecture", "name", "package", "sha256", "size", "version"}
)
IMAGE_LABEL_PREFIX: Final = "io.elsewindow.xpra-release"
REQUIRED_XPRA_PACKAGES: Final = (
    "xpra",
    "xpra-client",
    "xpra-client-gtk3",
    "xpra-codecs",
    "xpra-common",
    "xpra-server",
    "xpra-server-wayland",
    "xpra-wayland",
    "xpra-x11",
)
REQUIRED_APT_PACKAGES: Final = ("libva-drm2", "python3-opengl", "python3-venv")
PYTHON_PACKAGE_ROOT: Final = "usr/lib/python3/dist-packages/xpra"
CPYTHON_EXTENSION: Final = r"\.cpython-[0-9]+-x86_64-linux-gnu\.so"
COMMON_PACKAGE_CONTENT: Final = {
    "xpra-client": (re.compile(rf"{PYTHON_PACKAGE_ROOT}/client/base/client\.py"),),
    "xpra-client-gtk3": (
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/client/gtk3/opengl/client_window\.py"),
    ),
    "xpra-codecs": (
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/codecs/libva/__init__\.py"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/codecs/libva/encoder{CPYTHON_EXTENSION}"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/codecs/libva/decoder{CPYTHON_EXTENSION}"),
        re.compile(
            rf"{PYTHON_PACKAGE_ROOT}/codecs/libyuv/converter{CPYTHON_EXTENSION}"
        ),
    ),
    "xpra-common": (
        re.compile(r"usr/bin/xpra"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/codecs/checks\.py"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/codecs/video\.py"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/opengl/backing\.py"),
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/scripts/main\.py"),
    ),
    "xpra-server": (
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/server/core\.py"),
        re.compile(r"usr/share/xpra/css/10_header_bar\.css"),
    ),
    "xpra-wayland": (
        re.compile(
            rf"{PYTHON_PACKAGE_ROOT}/wayland/client/wait_for_display"
            rf"{CPYTHON_EXTENSION}"
        ),
    ),
    "xpra-x11": (
        re.compile(rf"{PYTHON_PACKAGE_ROOT}/x11/bindings/core{CPYTHON_EXTENSION}"),
    ),
}
DISTRO_PACKAGE_CONTENT: Final = {
    "ubuntu-26.04": {
        "xpra-server-wayland": (
            re.compile(
                rf"{PYTHON_PACKAGE_ROOT}/wayland/server/display{CPYTHON_EXTENSION}"
            ),
            re.compile(
                rf"{PYTHON_PACKAGE_ROOT}/wayland/server/events{CPYTHON_EXTENSION}"
            ),
            re.compile(
                rf"{PYTHON_PACKAGE_ROOT}/wayland/server/compositor{CPYTHON_EXTENSION}"
            ),
            re.compile(rf"{PYTHON_PACKAGE_ROOT}/wayland/server/seamless\.py"),
        ),
    },
    "debian-13": {},
}
COMMON_RUNTIME_IMPORTS: Final = (
    "OpenGL",
    "xpra.client.gtk3.opengl.client_window",
    "xpra.codecs.libva.decoder",
    "xpra.codecs.libva.encoder",
    "xpra.codecs.libyuv.converter",
    "xpra.opengl.backing",
    "xpra.server.core",
    "xpra.x11.bindings.core",
)
DISTRO_RUNTIME_IMPORTS: Final = {
    "ubuntu-26.04": (
        "xpra.wayland.server.compositor",
        "xpra.wayland.server.display",
        "xpra.wayland.server.events",
        "xpra.wayland.server.seamless",
    ),
    "debian-13": (),
}
SUPPORTED_SYSTEMS: Final = {
    ("debian", "13"): "debian-13",
    ("ubuntu", "26.04"): "ubuntu-26.04",
}
APT_GET: Final = "/usr/bin/apt-get"
DPKG: Final = "/usr/bin/dpkg"
DPKG_DEB: Final = "/usr/bin/dpkg-deb"
DPKG_QUERY: Final = "/usr/bin/dpkg-query"
ENV: Final = "/usr/bin/env"
INSTALL: Final = "/usr/bin/install"
MKTEMP: Final = "/usr/bin/mktemp"
PYTHON: Final = "/usr/bin/python3"
RM: Final = "/usr/bin/rm"
SHA256SUM: Final = "/usr/bin/sha256sum"
STAT: Final = "/usr/bin/stat"
SUDO: Final = "/usr/bin/sudo"
XPRA: Final = "/usr/bin/xpra"
ROOT_STAGE_PATTERN: Final = re.compile(r"^xpra-release-install\.[A-Za-z0-9]{10}$")
BINARY_NAME_PATTERN: Final = re.compile(
    r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?$"
)
APT_INSTALL_PATTERN: Final = re.compile(
    r"^Inst\s+(\S+)(?:\s+\[[^\]\r\n]+\])?\s+\((\S+)"
)


class ReleaseError(RuntimeError):
    """Raised when public release state fails the consumer contract."""


class InstallationCancelled(RuntimeError):
    """Raised when the operator declines an installed-package replacement."""


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """One immutable GitHub release asset."""

    asset_id: int
    name: str
    size: int
    sha256: str
    url: str


@dataclasses.dataclass(frozen=True, slots=True)
class ForkRelease:
    """One canonical package release from the Xpra fork."""

    release_id: int
    tag: str
    version: str
    commit: str
    published_at: str
    run_id: int
    attempt: int
    assets: tuple[ReleaseAsset, ...]

    def asset(self, name: str) -> ReleaseAsset:
        """Return one exact asset by name."""
        matches = tuple(asset for asset in self.assets if asset.name == name)
        if len(matches) != 1:
            raise ReleaseError(f"release does not contain one asset named {name}")
        return matches[0]

    def descriptor(self) -> dict[str, Any]:
        """Return canonical serializable release provenance."""
        return {
            "assets": {
                asset.name: {
                    "id": asset.asset_id,
                    "sha256": asset.sha256,
                    "size": asset.size,
                    "url": asset.url,
                }
                for asset in self.assets
            },
            "attempt": self.attempt,
            "commit": self.commit,
            "published_at": self.published_at,
            "release_id": self.release_id,
            "repository": REPOSITORY,
            "run_id": self.run_id,
            "schema": 1,
            "tag": self.tag,
            "version": self.version,
            "workflow": WORKFLOW,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class PackageRecord:
    """Validated package entry from one release tar."""

    filename: str
    package: str
    version: str
    architecture: str
    size: int
    sha256: str


@dataclasses.dataclass(frozen=True, slots=True)
class PackageArchive:
    """Validated release-tar manifest and package inventory."""

    distro: str
    version: str
    checkout_commit: str
    packages: tuple[PackageRecord, ...]
    manifest: Mapping[str, Any]


@dataclasses.dataclass(frozen=True, slots=True)
class SystemTarget:
    """One exact supported host distribution."""

    os_id: str
    version_id: str
    distro: str
    architecture: str


@dataclasses.dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One dpkg database entry owned by the Xpra source or namespace."""

    binary: str
    package: str
    source: str
    status: str
    version: str


@dataclasses.dataclass(frozen=True, slots=True)
class RootPackageStage:
    """One root-owned immutable copy of the validated transaction payload."""

    directory: Path
    archive: Path
    packages: tuple[Path, ...]


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
PurgeConfirmation = Callable[[tuple[InstalledPackage, ...]], bool]


def _plain_int(value: Any, name: str, *, positive: bool = True) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReleaseError(f"{name} is not an integer")
    if (positive and value <= 0) or (not positive and value < 0):
        raise ReleaseError(f"{name} is outside its allowed range")
    return value


def _plain_string(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise ReleaseError(f"{name} is not a safe nonempty string")
    return value


def _sha256(value: Any, name: str, *, prefixed: bool = False) -> str:
    text = _plain_string(value, name)
    if prefixed:
        prefix = "sha256:"
        if not text.startswith(prefix):
            raise ReleaseError(f"{name} does not use sha256")
        text = text.removeprefix(prefix)
    if SHA256_PATTERN.fullmatch(text) is None:
        raise ReleaseError(f"{name} is not a lowercase SHA-256 digest")
    return text


def _commit(value: Any, name: str) -> str:
    text = _plain_string(value, name)
    if COMMIT_PATTERN.fullmatch(text) is None:
        raise ReleaseError(f"{name} is not a full lowercase Git commit")
    return text


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReleaseError(f"{name} is not an object with string keys")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReleaseError(f"{name} keys differ: missing={missing}, extra={extra}")


def _timestamp(value: Any) -> tuple[str, dt.datetime]:
    text = _plain_string(value, "release published_at")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as error:
        raise ReleaseError("release published_at is invalid") from error
    if parsed.tzinfo is None:
        raise ReleaseError("release published_at lacks a timezone")
    return text, parsed.astimezone(dt.UTC)


def _safe_https_url(value: Any, name: str, hosts: frozenset[str]) -> str:
    text = _plain_string(value, name)
    parsed = urllib.parse.urlsplit(text)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ReleaseError(f"{name} is outside the allowed HTTPS boundary")
    return text


def parse_release(value: Any) -> tuple[ForkRelease, dt.datetime] | None:
    """Parse one API release, ignoring only clearly unrelated releases."""
    release = _mapping(value, "release")
    body = release.get("body")
    if not isinstance(body, str):
        if body is None:
            return None
        raise ReleaseError("release body has the wrong type")
    marker_count = body.count(TRANSACTION_PREFIX)
    matches = TRANSACTION_PATTERN.findall(body)
    if marker_count == 0:
        return None
    if marker_count != 1 or len(matches) != 1:
        raise ReleaseError("release has an ambiguous package transaction marker")
    try:
        transaction_value = json.loads(matches[0])
    except json.JSONDecodeError as error:
        raise ReleaseError(
            "release package transaction marker is invalid JSON"
        ) from error
    transaction = _mapping(transaction_value, "release transaction")
    _exact_keys(transaction, TRANSACTION_KEYS, "release transaction")
    if _plain_int(transaction["schema"], "release transaction schema") != 1:
        raise ReleaseError("release transaction schema is unsupported")
    if (
        _plain_string(transaction["owner"], "release transaction owner")
        != TRANSACTION_OWNER
    ):
        raise ReleaseError("release transaction owner is not canonical")
    if _plain_string(transaction["repository"], "release repository") != REPOSITORY:
        raise ReleaseError("release transaction repository is not canonical")
    if _plain_string(transaction["workflow"], "release workflow") != WORKFLOW:
        raise ReleaseError("release transaction workflow is not canonical")

    release_id = _plain_int(release.get("id"), "release id")
    run_id = _plain_int(transaction["run_id"], "release run id")
    attempt = _plain_int(transaction["attempt"], "release attempt")
    version = _plain_string(transaction["version"], "release version")
    commit = _commit(transaction["commit"], "release commit")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ReleaseError("canonical package release is not an ordinary release")
    if _plain_string(release.get("name"), "release name") != version:
        raise ReleaseError("release name does not match its package version")
    tag = _plain_string(release.get("tag_name"), "release tag")
    expected_tag = f"kogeler-deb-{version}-run{run_id}-attempt{attempt}"
    if tag != expected_tag:
        raise ReleaseError("release tag does not match its transaction")
    if _commit(release.get("target_commitish"), "release target") != commit:
        raise ReleaseError("release target does not match its transaction")
    published_at, published = _timestamp(release.get("published_at"))

    transaction_assets = _mapping(transaction["assets"], "transaction assets")
    if frozenset(transaction_assets) != frozenset(ASSET_NAMES):
        raise ReleaseError("release transaction has the wrong asset set")
    api_assets = release.get("assets")
    if not isinstance(api_assets, list) or len(api_assets) != len(ASSET_NAMES):
        raise ReleaseError("release API has the wrong asset set")
    parsed_assets: list[ReleaseAsset] = []
    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    for index, raw_asset in enumerate(api_assets):
        asset = _mapping(raw_asset, f"release asset {index}")
        name = _plain_string(asset.get("name"), f"release asset {index} name")
        if name not in ASSET_NAMES or name in seen_names:
            raise ReleaseError("release API contains an unknown or duplicate asset")
        asset_id = _plain_int(asset.get("id"), f"release asset {name} id")
        if asset_id in seen_ids:
            raise ReleaseError("release API contains a duplicate asset id")
        size = _plain_int(asset.get("size"), f"release asset {name} size")
        if size > MAX_ASSET_BYTES:
            raise ReleaseError(f"release asset {name} is too large")
        digest = _sha256(
            asset.get("digest"), f"release asset {name} digest", prefixed=True
        )
        url = _safe_https_url(
            asset.get("browser_download_url"),
            f"release asset {name} URL",
            frozenset({"github.com"}),
        )
        marker_asset = _mapping(transaction_assets[name], f"transaction asset {name}")
        _exact_keys(marker_asset, TRANSACTION_ASSET_KEYS, f"transaction asset {name}")
        if _plain_int(marker_asset["size"], f"transaction asset {name} size") != size:
            raise ReleaseError(
                f"release asset {name} size differs from its transaction"
            )
        marker_digest = _sha256(
            marker_asset["digest"], f"transaction asset {name} digest", prefixed=True
        )
        if marker_digest != digest:
            raise ReleaseError(
                f"release asset {name} digest differs from its transaction"
            )
        parsed_assets.append(ReleaseAsset(asset_id, name, size, digest, url))
        seen_names.add(name)
        seen_ids.add(asset_id)
    parsed_assets.sort(key=lambda item: item.name)
    return (
        ForkRelease(
            release_id,
            tag,
            version,
            commit,
            published_at,
            run_id,
            attempt,
            tuple(parsed_assets),
        ),
        published,
    )


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: BinaryIO,
        code: int,
        message: str,
        headers: Mapping[str, str],
        new_url: str,
    ) -> urllib.request.Request | None:
        _safe_https_url(new_url, "release redirect URL", ALLOWED_DOWNLOAD_HOSTS)
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


def default_opener() -> urllib.request.OpenerDirector:
    """Build the strict HTTPS opener used for public GitHub data."""
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _RestrictedRedirect()
    )


class GitHubApi:
    """Bounded standard-library GitHub API reader."""

    def __init__(
        self,
        *,
        token: str | None = None,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self._token = token
        self._opener = opener or default_opener()

    def get_json(self, url: str) -> Any:
        _safe_https_url(url, "GitHub API URL", frozenset({"api.github.com"}))
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "elsewindow-release/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > MAX_JSON_BYTES:
                    raise ReleaseError("GitHub API response is too large")
                payload = response.read(MAX_JSON_BYTES + 1)
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise ReleaseError("GitHub API request failed") from error
        if len(payload) > MAX_JSON_BYTES:
            raise ReleaseError("GitHub API response exceeded its byte limit")
        try:
            return json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReleaseError("GitHub API returned invalid JSON") from error

    def releases(self) -> tuple[Any, ...]:
        values: list[Any] = []
        for page in range(1, MAX_RELEASE_PAGES + 1):
            query = urllib.parse.urlencode(
                {"page": page, "per_page": RELEASES_PER_PAGE}
            )
            payload = self.get_json(f"{RELEASES_API}?{query}")
            if not isinstance(payload, list):
                raise ReleaseError("GitHub release collection is not a list")
            values.extend(payload)
            if len(payload) < RELEASES_PER_PAGE:
                return tuple(values)
        raise ReleaseError("GitHub release pagination exceeded its page limit")

    def verify_develop_commit(self, commit: str) -> None:
        selected = _commit(commit, "selected release commit")
        suffix = (
            f"{urllib.parse.quote(selected)}...{urllib.parse.quote(DEVELOP_BRANCH)}"
        )
        payload = _mapping(
            self.get_json(f"{COMPARE_API}/{suffix}"), "GitHub compare response"
        )
        merge_base = _mapping(payload.get("merge_base_commit"), "compare merge base")
        if _commit(merge_base.get("sha"), "compare merge base commit") != selected:
            raise ReleaseError("release commit is not in current develop history")
        status_value = _plain_string(payload.get("status"), "compare status")
        if status_value not in {"ahead", "identical"}:
            raise ReleaseError("release commit is not an ancestor of current develop")


def resolve_latest_release(api: GitHubApi) -> ForkRelease:
    """Resolve and verify the unique newest canonical fork package release."""
    candidates: list[tuple[dt.datetime, ForkRelease]] = []
    seen_ids: set[int] = set()
    seen_tags: set[str] = set()
    for value in api.releases():
        release = _mapping(value, "release collection entry")
        release_id = _plain_int(release.get("id"), "release collection id")
        tag = _plain_string(release.get("tag_name"), "release collection tag")
        if release_id in seen_ids or tag in seen_tags:
            raise ReleaseError(
                "GitHub release collection contains duplicate identities"
            )
        seen_ids.add(release_id)
        seen_tags.add(tag)
        parsed = parse_release(release)
        if parsed is not None:
            candidate, published = parsed
            candidates.append((published, candidate))
    if not candidates:
        raise ReleaseError("no canonical Xpra fork package release exists")
    candidates.sort(key=lambda item: (item[0], item[1].release_id), reverse=True)
    newest_time, newest = candidates[0]
    if len(candidates) > 1:
        next_time, next_release = candidates[1]
        if newest_time == next_time and newest.release_id == next_release.release_id:
            raise ReleaseError("newest canonical release is ambiguous")
    api.verify_develop_commit(newest.commit)
    return newest


def verify_release_is_current(selected: ForkRelease, api: GitHubApi) -> None:
    """Fail if fresh discovery no longer resolves to the selected release."""
    current = resolve_latest_release(api)
    if current != selected:
        raise ReleaseError("a newer or changed canonical package release is available")


def image_release_labels(
    release: ForkRelease, asset: ReleaseAsset, distro: str
) -> dict[str, str]:
    """Return the complete immutable image-cache binding for one asset."""
    if ASSET_FOR_DISTRO.get(distro) != asset.name:
        raise ReleaseError("release asset does not match the selected distribution")
    prefix = IMAGE_LABEL_PREFIX
    return {
        f"{prefix}.asset-id": str(asset.asset_id),
        f"{prefix}.asset-name": asset.name,
        f"{prefix}.asset-sha256": asset.sha256,
        f"{prefix}.asset-size": str(asset.size),
        f"{prefix}.commit": release.commit,
        f"{prefix}.distro": distro,
        f"{prefix}.release-id": str(release.release_id),
        f"{prefix}.tag": release.tag,
        f"{prefix}.version": release.version,
    }


def verify_image_release_labels(
    labels: Mapping[str, Any],
    release: ForkRelease,
    asset: ReleaseAsset,
    distro: str,
) -> None:
    """Reject a cached image unless every release label is exact."""
    expected = image_release_labels(release, asset, distro)
    for key, value in expected.items():
        if labels.get(key) != value:
            raise ReleaseError(f"cached image release label differs: {key}")


def canonical_json(value: Any) -> str:
    """Render stable JSON for descriptors and tests."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def verify_file(path: Path, *, size: int, sha256: str) -> None:
    """Verify one owned regular file against immutable public metadata."""
    expected_sha = _sha256(sha256, "expected file digest")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseError(f"cannot open release file: {path}") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
            or details.st_size != size
        ):
            raise ReleaseError("release file identity or size is invalid")
        digest = hashlib.sha256()
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != expected_sha:
            raise ReleaseError("release file digest does not match")
    finally:
        os.close(descriptor)


def download_asset(
    asset: ReleaseAsset,
    destination: Path,
    *,
    opener: urllib.request.OpenerDirector | None = None,
) -> None:
    """Download one exact release asset into a new private file."""
    if destination.exists() or destination.is_symlink():
        raise ReleaseError(f"release destination already exists: {destination}")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ReleaseError("release destination parent is unsafe")
    _safe_https_url(asset.url, "release asset URL", frozenset({"github.com"}))
    selected_opener = opener or default_opener()
    request = urllib.request.Request(
        asset.url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "elsewindow-release/1",
        },
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    complete = False
    try:
        descriptor = os.open(destination, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        with selected_opener.open(request, timeout=HTTP_TIMEOUT) as response:
            final_url = response.geturl()
            _safe_https_url(final_url, "release download URL", ALLOWED_DOWNLOAD_HOSTS)
            header_size = response.headers.get("Content-Length")
            if header_size is not None and int(header_size) != asset.size:
                raise ReleaseError("release download Content-Length is inconsistent")
            digest = hashlib.sha256()
            received = 0
            with os.fdopen(os.dup(descriptor), "wb") as output:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > asset.size or received > MAX_ASSET_BYTES:
                        raise ReleaseError("release download exceeded its size")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if received != asset.size or digest.hexdigest() != asset.sha256:
                raise ReleaseError("release download does not match its metadata")
        complete = True
    except (OSError, ValueError, urllib.error.URLError) as error:
        raise ReleaseError("release asset download failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not complete:
            destination.unlink(missing_ok=True)


def _safe_member_name(name: str) -> str:
    try:
        pure = PurePosixPath(name)
    except ValueError as error:
        raise ReleaseError("release archive contains an invalid member name") from error
    if (
        not name
        or pure.is_absolute()
        or len(pure.parts) != 1
        or pure.parts[0] in {".", ".."}
        or "\x00" in name
    ):
        raise ReleaseError("release archive member escapes its flat namespace")
    return name


def _read_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int
) -> bytes:
    if member.size > limit:
        raise ReleaseError(f"release archive member is too large: {member.name}")
    source = archive.extractfile(member)
    if source is None:
        raise ReleaseError(f"cannot read release archive member: {member.name}")
    with source:
        value = source.read(limit + 1)
    if len(value) != member.size or len(value) > limit:
        raise ReleaseError(f"release archive member size changed: {member.name}")
    return value


def _digest_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int
) -> tuple[int, str]:
    if member.size > limit:
        raise ReleaseError(f"release archive member is too large: {member.name}")
    source = archive.extractfile(member)
    if source is None:
        raise ReleaseError(f"cannot read release archive member: {member.name}")
    size = 0
    digest = hashlib.sha256()
    with source:
        while block := source.read(1024 * 1024):
            size += len(block)
            if size > member.size or size > limit:
                raise ReleaseError(
                    f"release archive member size changed: {member.name}"
                )
            digest.update(block)
    if size != member.size:
        raise ReleaseError(f"release archive member size changed: {member.name}")
    return size, digest.hexdigest()


def _parse_sha256sums(value: bytes) -> dict[str, str]:
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as error:
        raise ReleaseError("SHA256SUMS is not ASCII") from error
    if not text.endswith("\n"):
        raise ReleaseError("SHA256SUMS lacks a final newline")
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        digest, separator, filename = line.partition("  ")
        name = _safe_member_name(filename)
        if separator != "  " or SHA256_PATTERN.fullmatch(digest) is None:
            raise ReleaseError("SHA256SUMS contains an invalid line")
        if name in parsed:
            raise ReleaseError("SHA256SUMS contains a duplicate filename")
        parsed[name] = digest
    if not parsed:
        raise ReleaseError("SHA256SUMS is empty")
    return parsed


def _package_record(value: Any, index: int, version: str) -> PackageRecord:
    package = _mapping(value, f"manifest package {index}")
    _exact_keys(package, PACKAGE_KEYS, f"manifest package {index}")
    filename = _safe_member_name(
        _plain_string(package["name"], f"manifest package {index} filename")
    )
    match = PACKAGE_FILE_PATTERN.fullmatch(filename)
    if match is None:
        raise ReleaseError(f"manifest package {index} has an invalid filename")
    package_name = _plain_string(package["package"], f"manifest package {index} name")
    if (
        PACKAGE_PATTERN.fullmatch(package_name) is None
        or match.group(1) != package_name
    ):
        raise ReleaseError(f"manifest package {index} has an invalid package name")
    package_version = _plain_string(
        package["version"], f"manifest package {index} version"
    )
    if package_version != version or match.group(2) != version:
        raise ReleaseError(f"manifest package {index} has a mixed version")
    architecture = _plain_string(
        package["architecture"], f"manifest package {index} architecture"
    )
    if architecture != "amd64":
        raise ReleaseError(f"manifest package {index} has a wrong architecture")
    size = _plain_int(package["size"], f"manifest package {index} size")
    if size > MAX_PACKAGE_BYTES:
        raise ReleaseError(f"manifest package {index} is too large")
    digest = _sha256(package["sha256"], f"manifest package {index} digest")
    return PackageRecord(
        filename, package_name, package_version, architecture, size, digest
    )


def validate_package_archive(
    path: Path,
    *,
    release: ForkRelease,
    asset: ReleaseAsset,
    distro: str,
) -> PackageArchive:
    """Validate a complete plain-tar package asset without extracting it."""
    if ASSET_FOR_DISTRO.get(distro) != asset.name:
        raise ReleaseError("release asset does not match the selected distribution")
    verify_file(path, size=asset.size, sha256=asset.sha256)
    try:
        with path.open("rb") as raw, tarfile.open(fileobj=raw, mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseError("release archive member count is invalid")
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                name = _safe_member_name(member.name)
                if name in by_name:
                    raise ReleaseError("release archive contains duplicate members")
                if not member.isreg() or member.issparse() or member.pax_headers:
                    raise ReleaseError("release archive contains a non-plain member")
                if member.size < 0 or member.size > MAX_PACKAGE_BYTES:
                    raise ReleaseError("release archive member size is invalid")
                by_name[name] = member
            if "manifest.json" not in by_name or "SHA256SUMS" not in by_name:
                raise ReleaseError("release archive lacks its metadata files")
            manifest_bytes = _read_member(
                archive, by_name["manifest.json"], MAX_MANIFEST_BYTES
            )
            sums_bytes = _read_member(
                archive, by_name["SHA256SUMS"], MAX_MANIFEST_BYTES
            )
            package_digests = {
                name: _digest_member(archive, member, MAX_PACKAGE_BYTES)
                for name, member in by_name.items()
                if name.endswith(".deb")
            }
    except (OSError, tarfile.TarError) as error:
        raise ReleaseError("release asset is not a valid plain tar") from error
    if frozenset(by_name) != frozenset(
        {"manifest.json", "SHA256SUMS", *package_digests}
    ):
        raise ReleaseError("release archive contains an unexpected member")
    try:
        manifest_value = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError("release manifest is invalid JSON") from error
    manifest = _mapping(manifest_value, "release manifest")
    _exact_keys(manifest, MANIFEST_KEYS, "release manifest")
    if _plain_int(manifest["schema"], "release manifest schema") != 2:
        raise ReleaseError("release manifest schema is unsupported")
    if _plain_string(manifest["distro"], "release manifest distro") != distro:
        raise ReleaseError("release manifest has the wrong distribution")
    if (
        _plain_string(manifest["architecture"], "release manifest architecture")
        != "amd64"
    ):
        raise ReleaseError("release manifest has the wrong architecture")
    version = _plain_string(manifest["debian_version"], "release manifest version")
    if version != release.version:
        raise ReleaseError("release manifest version differs from the release")
    checkout = _commit(manifest["checkout_commit"], "release manifest checkout")
    if checkout != release.commit:
        raise ReleaseError("release manifest checkout differs from the release")
    raw_packages = manifest["packages"]
    if not isinstance(raw_packages, list) or not raw_packages:
        raise ReleaseError("release manifest packages are invalid")
    packages = tuple(
        _package_record(value, index, version)
        for index, value in enumerate(raw_packages)
    )
    if tuple(sorted(item.filename for item in packages)) != tuple(
        item.filename for item in packages
    ):
        raise ReleaseError("release manifest packages are not sorted")
    if len({item.filename for item in packages}) != len(packages) or len(
        {item.package for item in packages}
    ) != len(packages):
        raise ReleaseError("release manifest contains duplicate packages")
    expected_names = {item.filename for item in packages}
    if set(package_digests) != expected_names:
        raise ReleaseError("release archive package members differ from its manifest")
    sums = _parse_sha256sums(sums_bytes)
    if set(sums) != expected_names:
        raise ReleaseError("SHA256SUMS package set differs from the manifest")
    for package in packages:
        size, digest = package_digests[package.filename]
        if (
            size != package.size
            or digest != package.sha256
            or sums[package.filename] != package.sha256
        ):
            raise ReleaseError(
                f"package bytes differ from metadata: {package.filename}"
            )
    return PackageArchive(distro, version, checkout, packages, manifest)


def run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one fixed system command with deterministic noninteractive text I/O."""
    if not arguments or any(
        not isinstance(item, str)
        or not item
        or any(character in item for character in ("\x00", "\r", "\n"))
        for item in arguments
    ):
        raise ReleaseError("system command contains an unsafe argument")
    environment = os.environ.copy()
    environment.update(
        {
            "DEBIAN_FRONTEND": "noninteractive",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )
    try:
        return subprocess.run(
            tuple(arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            check=False,
        )
    except (OSError, UnicodeError) as error:
        raise ReleaseError("cannot execute a required system command") from error


def run_sudo_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one fixed command through sudo while keeping its terminal prompt."""
    return run_command(
        (
            SUDO,
            "--",
            ENV,
            "DEBIAN_FRONTEND=noninteractive",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            *arguments,
        )
    )


def require_sudo() -> None:
    """Require one immutable root-owned sudo executable before network access."""
    try:
        details = Path(SUDO).stat()
    except OSError as error:
        raise ReleaseError("installation requires /usr/bin/sudo") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_mode & 0o022
        or not os.access(SUDO, os.X_OK)
    ):
        raise ReleaseError("installation requires a trusted /usr/bin/sudo")


def _checked(
    runner: CommandRunner, arguments: Sequence[str], purpose: str
) -> subprocess.CompletedProcess[str]:
    completed = runner(tuple(arguments))
    if not isinstance(completed, subprocess.CompletedProcess):
        raise ReleaseError(f"{purpose} returned an invalid command result")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().replace("\x00", "?")
        suffix = f": {detail[-4096:]}" if detail else ""
        raise ReleaseError(
            f"{purpose} failed with status {completed.returncode}{suffix}"
        )
    if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
        raise ReleaseError(f"{purpose} returned non-text output")
    return completed


def parse_os_release(value: str) -> dict[str, str]:
    """Parse the strict KEY=VALUE subset required from /etc/os-release."""
    parsed: dict[str, str] = {}
    for line_number, line in enumerate(value.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if (
            separator != "="
            or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None
            or key in parsed
        ):
            raise ReleaseError(f"os-release line {line_number} is invalid")
        try:
            tokens = shlex.split(raw, comments=False, posix=True)
        except ValueError as error:
            raise ReleaseError(f"os-release line {line_number} is invalid") from error
        if len(tokens) != 1 or any(character in tokens[0] for character in "\r\n\x00"):
            raise ReleaseError(f"os-release line {line_number} is invalid")
        parsed[key] = tokens[0]
    return parsed


def detect_system(
    *,
    os_release_path: Path = Path("/etc/os-release"),
    runner: CommandRunner = run_command,
) -> SystemTarget:
    """Refuse unsupported hosts before network access."""
    try:
        details = os_release_path.stat()
        if not stat.S_ISREG(details.st_mode):
            raise ReleaseError("/etc/os-release is not a regular file")
        os_release = parse_os_release(os_release_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ReleaseError("cannot read /etc/os-release") from error
    os_id = os_release.get("ID", "")
    version_id = os_release.get("VERSION_ID", "")
    distro = SUPPORTED_SYSTEMS.get((os_id, version_id))
    if distro is None:
        raise ReleaseError("installation supports only Debian 13 or Ubuntu 26.04")
    architecture = _checked(
        runner, (DPKG, "--print-architecture"), "architecture detection"
    ).stdout.strip()
    if architecture != "amd64":
        raise ReleaseError("installation supports only amd64")
    return SystemTarget(os_id, version_id, distro, architecture)


def select_required_packages(archive: PackageArchive) -> PackageArchive:
    """Select the one symmetric package set consumed by both roles and OSes."""
    available = {package.package: package for package in archive.packages}
    if not set(REQUIRED_XPRA_PACKAGES).issubset(available):
        raise ReleaseError(
            f"release lacks required packages: expected={list(REQUIRED_XPRA_PACKAGES)}, "
            f"actual={sorted(available)}"
        )
    selected = tuple(available[package] for package in REQUIRED_XPRA_PACKAGES)
    return PackageArchive(
        archive.distro,
        archive.version,
        archive.checkout_commit,
        selected,
        archive.manifest,
    )


def extract_package_files(
    archive_path: Path, archive: PackageArchive, destination: Path
) -> tuple[Path, ...]:
    """Copy verified DEB members into one new private directory."""
    if destination.exists() or destination.is_symlink():
        raise ReleaseError("package extraction destination already exists")
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    expected = {record.filename: record for record in archive.packages}
    written: list[Path] = []
    try:
        with (
            archive_path.open("rb") as raw,
            tarfile.open(fileobj=raw, mode="r:") as tar,
        ):
            members = {member.name: member for member in tar.getmembers()}
            for filename in sorted(expected):
                record = expected[filename]
                member = members.get(filename)
                if member is None or not member.isreg() or member.size != record.size:
                    raise ReleaseError(f"verified package member changed: {filename}")
                source = tar.extractfile(member)
                if source is None:
                    raise ReleaseError(f"cannot read verified package: {filename}")
                target = destination / filename
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(target, flags, 0o600)
                digest = hashlib.sha256()
                size = 0
                try:
                    os.fchmod(descriptor, 0o600)
                    with source, os.fdopen(os.dup(descriptor), "wb") as output:
                        while block := source.read(1024 * 1024):
                            size += len(block)
                            if size > record.size:
                                raise ReleaseError(
                                    f"verified package size changed: {filename}"
                                )
                            digest.update(block)
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    os.close(descriptor)
                if size != record.size or digest.hexdigest() != record.sha256:
                    raise ReleaseError(f"verified package bytes changed: {filename}")
                written.append(target)
    except (OSError, tarfile.TarError) as error:
        raise ReleaseError("cannot extract verified package archive") from error
    return tuple(written)


def _deb_field(path: Path, field: str, runner: CommandRunner) -> str:
    completed = _checked(
        runner,
        (DPKG_DEB, "--field", str(path), field),
        f"DEB {field} validation",
    )
    value = completed.stdout.strip()
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise ReleaseError(f"DEB {field} metadata is invalid")
    return value


def _deb_regular_files(path: Path, runner: CommandRunner) -> frozenset[str]:
    """Return the exact regular-file paths reported by one validated DEB."""
    completed = _checked(
        runner,
        (DPKG_DEB, "--contents", str(path)),
        "DEB content validation",
    )
    files: set[str] = set()
    seen: set[str] = set()
    for line_number, line in enumerate(completed.stdout.splitlines(), 1):
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            raise ReleaseError(f"DEB content line {line_number} is invalid")
        mode, _owner, size, _date, _time, raw_name = fields
        if (
            re.fullmatch(r"[bcdlps-][rwxStTs-]{9}", mode) is None
            or not size.isdecimal()
            or not raw_name.startswith("./")
        ):
            raise ReleaseError(f"DEB content line {line_number} is invalid")
        if mode.startswith("l"):
            name, separator, target = raw_name.partition(" -> ")
            if separator != " -> " or not target:
                raise ReleaseError(f"DEB content line {line_number} is invalid")
        else:
            name = raw_name
            if " -> " in name:
                raise ReleaseError(f"DEB content line {line_number} is invalid")
        relative = name.removeprefix("./").rstrip("/")
        if not relative:
            continue
        try:
            pure = PurePosixPath(relative)
        except ValueError as error:
            raise ReleaseError(f"DEB content line {line_number} is invalid") from error
        if (
            pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or "\x00" in relative
            or relative in seen
        ):
            raise ReleaseError(f"DEB content line {line_number} is invalid")
        seen.add(relative)
        if mode.startswith("-"):
            files.add(relative)
    if not seen:
        raise ReleaseError("DEB content listing is empty")
    return frozenset(files)


def _required_package_content(
    distro: str,
) -> dict[str, tuple[re.Pattern[str], ...]]:
    try:
        distro_requirements = DISTRO_PACKAGE_CONTENT[distro]
    except KeyError as error:
        raise ReleaseError("package content distribution is unsupported") from error
    requirements = dict(COMMON_PACKAGE_CONTENT)
    for package, patterns in distro_requirements.items():
        requirements[package] = (*requirements.get(package, ()), *patterns)
    if not set(requirements).issubset(REQUIRED_XPRA_PACKAGES):
        raise RuntimeError("package content requirements exceed the selected DEB set")
    return requirements


def verify_deb_metadata(
    archive: PackageArchive,
    paths: Sequence[Path],
    *,
    runner: CommandRunner = run_command,
) -> None:
    """Bind DEB controls and required runtime files to the validated manifest."""
    by_filename = {path.name: path for path in paths}
    if set(by_filename) != {record.filename for record in archive.packages}:
        raise ReleaseError("extracted DEB set differs from the manifest")
    requirements = _required_package_content(archive.distro)
    for record in archive.packages:
        path = by_filename[record.filename]
        if _deb_field(path, "Package", runner) != record.package:
            raise ReleaseError(f"DEB package name differs: {record.filename}")
        if _deb_field(path, "Version", runner) != record.version:
            raise ReleaseError(f"DEB package version differs: {record.filename}")
        if _deb_field(path, "Architecture", runner) != record.architecture:
            raise ReleaseError(f"DEB package architecture differs: {record.filename}")
        files = _deb_regular_files(path, runner)
        patterns = requirements.get(record.package, ())
        for pattern in patterns:
            matches = tuple(name for name in files if pattern.fullmatch(name))
            if len(matches) != 1:
                raise ReleaseError(
                    "DEB package lacks one exact required runtime file: "
                    f"package={record.package}, pattern={pattern.pattern!r}, "
                    f"matches={list(matches)}"
                )


def _root_stage_path(value: str, parent: Path) -> Path:
    """Validate the exact directory name returned by root-owned mktemp."""
    lines = value.splitlines()
    if (
        len(lines) != 1
        or not lines[0]
        or lines[0] != lines[0].strip()
        or "\x00" in lines[0]
    ):
        raise ReleaseError("root staging directory path is invalid")
    value = lines[0]
    path = Path(value)
    if (
        not path.is_absolute()
        or not parent.is_absolute()
        or path.parent != parent
        or ROOT_STAGE_PATTERN.fullmatch(path.name) is None
    ):
        raise ReleaseError("root staging directory is outside its private boundary")
    return path


def _root_stat(
    path: Path, runner: CommandRunner
) -> tuple[str, int, int, int, int, int]:
    """Return one root-observed filesystem identity without following links."""
    completed = _checked(
        runner,
        (
            STAT,
            "--format=%F\t%a\t%u\t%g\t%h\t%s",
            "--",
            str(path),
        ),
        "root staging identity check",
    )
    fields = completed.stdout.strip().split("\t")
    if len(fields) != 6:
        raise ReleaseError("root staging identity is invalid")
    kind, mode_text, uid_text, gid_text, links_text, size_text = fields
    try:
        mode = int(mode_text, 8)
        uid = int(uid_text)
        gid = int(gid_text)
        links = int(links_text)
        size = int(size_text)
    except ValueError as error:
        raise ReleaseError("root staging identity is invalid") from error
    if min(mode, uid, gid, links, size) < 0:
        raise ReleaseError("root staging identity is invalid")
    return kind, mode, uid, gid, links, size


def _verify_root_directory(path: Path, runner: CommandRunner) -> None:
    kind, mode, uid, gid, _links, _size = _root_stat(path, runner)
    if kind != "directory" or mode != 0o700 or uid != 0 or gid != 0:
        raise ReleaseError("root staging directory is not private and root-owned")


def _verify_root_file(
    path: Path,
    *,
    size: int,
    sha256: str,
    runner: CommandRunner,
) -> None:
    kind, mode, uid, gid, links, observed_size = _root_stat(path, runner)
    if (
        kind != "regular file"
        or mode != 0o600
        or uid != 0
        or gid != 0
        or links != 1
        or observed_size != size
    ):
        raise ReleaseError("root staging file identity or size differs")
    completed = _checked(
        runner,
        (SHA256SUM, "--", str(path)),
        "root staging checksum validation",
    )
    if completed.stdout.strip() != f"{sha256}  {path}":
        raise ReleaseError("root staging file checksum differs")


def _install_root_file(
    source: Path,
    destination: Path,
    *,
    size: int,
    sha256: str,
    runner: CommandRunner,
) -> None:
    _checked(
        runner,
        (
            INSTALL,
            "--mode=0600",
            "--owner=0",
            "--group=0",
            "--",
            str(source),
            str(destination),
        ),
        "root staging file publication",
    )
    _verify_root_file(destination, size=size, sha256=sha256, runner=runner)


def create_root_package_stage(
    *,
    parent: Path,
    asset: ReleaseAsset,
    archive_path: Path,
    archive: PackageArchive,
    package_paths: Sequence[Path],
    runner: CommandRunner,
) -> RootPackageStage:
    """Copy and revalidate the complete transaction below a root-owned path."""
    completed = _checked(
        runner,
        (
            MKTEMP,
            "--directory",
            f"--tmpdir={parent}",
            "xpra-release-install.XXXXXXXXXX",
        ),
        "root staging directory creation",
    )
    directory = _root_stage_path(completed.stdout, parent)
    _verify_root_directory(directory, runner)
    records = {record.filename: record for record in archive.packages}
    by_filename = {path.name: path for path in package_paths}
    if set(by_filename) != set(records):
        raise ReleaseError("validated package paths differ before root staging")
    try:
        root_archive = directory / asset.name
        _install_root_file(
            archive_path,
            root_archive,
            size=asset.size,
            sha256=asset.sha256,
            runner=runner,
        )
        root_packages: list[Path] = []
        for filename in sorted(records):
            record = records[filename]
            destination = directory / filename
            _install_root_file(
                by_filename[filename],
                destination,
                size=record.size,
                sha256=record.sha256,
                runner=runner,
            )
            root_packages.append(destination)
        verify_deb_metadata(archive, root_packages, runner=runner)
    except Exception:
        remove_root_stage(directory, parent=parent, runner=runner)
        raise
    return RootPackageStage(directory, root_archive, tuple(root_packages))


def remove_root_stage(directory: Path, *, parent: Path, runner: CommandRunner) -> None:
    """Remove only one validated root staging directory."""
    checked = _root_stage_path(str(directory), parent)
    _verify_root_directory(checked, runner)
    _checked(
        runner,
        (
            RM,
            "--recursive",
            "--force",
            "--one-file-system",
            "--",
            str(checked),
        ),
        "root staging cleanup",
    )


def list_xpra_packages(
    *, runner: CommandRunner = run_command
) -> tuple[InstalledPackage, ...]:
    """Return every installed or config-retaining Xpra-owned binary package."""
    output = _checked(
        runner,
        (
            DPKG_QUERY,
            "--show",
            (
                "--showformat=${binary:Package}\\t${Package}\\t"
                "${source:Package}\\t${db:Status-Abbrev}\\t${Version}\\n"
            ),
        ),
        "dpkg inventory",
    ).stdout
    selected: list[InstalledPackage] = []
    for line_number, line in enumerate(output.splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 5:
            raise ReleaseError(f"dpkg inventory line {line_number} is invalid")
        binary, package, source, status_value, version = fields
        if (
            BINARY_NAME_PATTERN.fullmatch(binary) is None
            or BINARY_NAME_PATTERN.fullmatch(package) is None
            or source
            and BINARY_NAME_PATTERN.fullmatch(source) is None
            or len(status_value) != 3
        ):
            raise ReleaseError(f"dpkg inventory line {line_number} is invalid")
        retained = status_value[1] != "n"
        namespace_owned = package == "xpra" or package.startswith("xpra-")
        if retained and (namespace_owned or source == "xpra"):
            selected.append(
                InstalledPackage(binary, package, source, status_value, version)
            )
    selected.sort(key=lambda item: (item.package, item.binary))
    if len({item.binary for item in selected}) != len(selected):
        raise ReleaseError("dpkg inventory contains duplicate Xpra packages")
    return tuple(selected)


def confirm_xpra_replacement(
    packages: tuple[InstalledPackage, ...],
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> bool:
    """Show the exact Xpra inventory and require an interactive affirmative."""
    if not packages:
        return True
    terminal_input: TextIO | None = None
    terminal_output: TextIO | None = None
    if input_stream is None and output_stream is None:
        try:
            terminal_input = open(  # noqa: SIM115 - closed in shared finally
                "/dev/tty",
                "r",
                encoding="utf-8",
                errors="strict",
            )
            try:
                terminal_output = open(  # noqa: SIM115 - closed in shared finally
                    "/dev/tty",
                    "w",
                    encoding="utf-8",
                    errors="strict",
                    buffering=1,
                )
            except OSError:
                terminal_input.close()
                terminal_input = None
                raise
        except OSError:
            terminal_output = None
    source = input_stream or terminal_input or sys.stdin
    destination = output_stream or terminal_output or sys.stderr
    try:
        print(
            "The following installed Xpra packages and retained configurations "
            "will be purged:",
            file=destination,
        )
        for package in packages:
            print(
                f"  - {package.binary} {package.version} "
                f"(dpkg status: {package.status.strip()})",
                file=destination,
            )
        print(
            "Purge this exact list and install the newest Xpra release? [y/N]: ",
            end="",
            file=destination,
            flush=True,
        )
        answer = source.readline()
    except (OSError, UnicodeError):
        return False
    finally:
        if terminal_output is not None:
            terminal_output.close()
        if terminal_input is not None:
            terminal_input.close()
    return answer.strip().casefold() in {"y", "yes"}


def recheck_confirmed_inventory(
    confirmed: tuple[InstalledPackage, ...],
    *,
    runner: CommandRunner = run_command,
) -> tuple[InstalledPackage, ...]:
    """Require the final pre-mutation inventory to equal the confirmed list."""
    current = list_xpra_packages(runner=runner)
    if current != confirmed:
        raise ReleaseError(
            "installed Xpra package inventory changed after confirmation"
        )
    return current


def simulate_install(
    paths: Sequence[Path], version: str, *, runner: CommandRunner = run_command
) -> None:
    """Require APT to plan the exact local Xpra set and runtime dependencies."""
    completed = _checked(
        runner,
        (
            APT_GET,
            "--simulate",
            "--no-install-recommends",
            "--reinstall",
            "--allow-downgrades",
            "install",
            *REQUIRED_APT_PACKAGES,
            *(str(path) for path in paths),
        ),
        "APT installation simulation",
    )
    planned: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        match = APT_INSTALL_PATTERN.match(line)
        if match is None:
            continue
        binary, planned_version = match.groups()
        package = binary.partition(":")[0]
        if package == "xpra" or package.startswith("xpra-"):
            if package in planned:
                raise ReleaseError("APT simulation contains duplicate Xpra packages")
            planned[package] = planned_version
    if set(planned) != set(REQUIRED_XPRA_PACKAGES) or any(
        planned[package] != version for package in planned
    ):
        raise ReleaseError("APT simulation did not select the exact local Xpra set")


def refresh_apt_metadata(*, runner: CommandRunner = run_command) -> None:
    """Refresh configured distribution dependency metadata before simulation."""
    _checked(runner, (APT_GET, "update"), "APT metadata refresh")


def purge_xpra_packages(
    packages: Sequence[InstalledPackage], *, runner: CommandRunner = run_command
) -> None:
    """Purge only the exact preflight inventory and prove no residue remains."""
    names = tuple(package.binary for package in packages)
    if names:
        _checked(
            runner,
            (APT_GET, "purge", "--yes", *names),
            "APT Xpra purge",
        )
    remaining = list_xpra_packages(runner=runner)
    if remaining:
        raise ReleaseError("Xpra packages or configuration remain after purge")


def install_local_packages(
    paths: Sequence[Path], *, runner: CommandRunner = run_command
) -> None:
    """Install one complete local-DEB transaction and its required runtime."""
    _checked(
        runner,
        (
            APT_GET,
            "install",
            "--yes",
            "--no-install-recommends",
            *REQUIRED_APT_PACKAGES,
            *(str(path) for path in paths),
        ),
        "APT local Xpra installation",
    )


def verify_installed_system(
    version: str,
    distro: str,
    *,
    runner: CommandRunner = run_command,
    root: Path = Path("/"),
) -> tuple[InstalledPackage, ...]:
    """Verify exact package inventory plus the commands and modules we consume."""
    try:
        distro_imports = DISTRO_RUNTIME_IMPORTS[distro]
    except KeyError as error:
        raise ReleaseError("installed Xpra distribution is unsupported") from error
    packages = list_xpra_packages(runner=runner)
    if tuple(package.package for package in packages) != REQUIRED_XPRA_PACKAGES:
        raise ReleaseError("installed Xpra package set is not exact")
    if any(
        package.status != "ii " or package.version != version for package in packages
    ):
        raise ReleaseError("installed Xpra package status or version differs")
    audit = _checked(runner, (DPKG, "--audit"), "dpkg audit")
    if audit.stdout.strip() or audit.stderr.strip():
        raise ReleaseError("dpkg audit reported package state")
    xpra_path = root / XPRA.removeprefix("/")
    css_path = root / "usr/share/xpra/css"
    if xpra_path.is_symlink() or not xpra_path.is_file():
        raise ReleaseError("installed Xpra command is missing")
    if css_path.is_symlink() or not css_path.is_dir() or not any(css_path.iterdir()):
        raise ReleaseError("installed Xpra shared CSS assets are missing")
    _checked(
        runner,
        (
            PYTHON,
            "-c",
            "; ".join(
                f"import {module}"
                for module in (*COMMON_RUNTIME_IMPORTS, *distro_imports)
            ),
        ),
        "Xpra required runtime import",
    )
    _checked(runner, (XPRA, "--version"), "Xpra version command")
    return packages


def install_latest_release(
    *,
    confirmation: PurgeConfirmation | None = None,
    runner: CommandRunner = run_command,
    privileged_runner: CommandRunner | None = None,
    api: GitHubApi | None = None,
    os_release_path: Path = Path("/etc/os-release"),
    euid: int | None = None,
    staging_parent: Path = Path("/var/tmp"),
) -> dict[str, Any]:
    """Perform the destructive, validated newest-release package transaction."""
    effective_uid = os.geteuid() if euid is None else euid
    if (
        not isinstance(effective_uid, int)
        or isinstance(effective_uid, bool)
        or effective_uid < 0
    ):
        raise ReleaseError("effective UID is invalid")
    target = detect_system(os_release_path=os_release_path, runner=runner)
    confirmed_inventory = list_xpra_packages(runner=runner)
    confirm = confirm_xpra_replacement if confirmation is None else confirmation
    if confirmed_inventory and not confirm(confirmed_inventory):
        raise InstallationCancelled("installation cancelled; no changes were made")
    if effective_uid == 0:
        transaction_runner = runner
    elif privileged_runner is not None:
        transaction_runner = privileged_runner
    else:
        require_sudo()
        transaction_runner = run_sudo_command
    selected_api = api or GitHubApi(token=os.environ.get("GITHUB_TOKEN") or None)
    release = resolve_latest_release(selected_api)
    asset = release.asset(ASSET_FOR_DISTRO[target.distro])
    try:
        stage = Path(
            tempfile.mkdtemp(prefix="xpra-release-download-", dir=staging_parent)
        )
        stage.chmod(0o700)
    except OSError as error:
        raise ReleaseError("cannot create private installer staging") from error
    mutation_started = False
    pre_purge: tuple[InstalledPackage, ...] = ()
    root_payload: RootPackageStage | None = None
    try:
        archive_path = stage / asset.name
        download_asset(asset, archive_path)
        complete_archive = validate_package_archive(
            archive_path, release=release, asset=asset, distro=target.distro
        )
        archive = select_required_packages(complete_archive)
        package_paths = extract_package_files(archive_path, archive, stage / "packages")
        verify_deb_metadata(archive, package_paths, runner=runner)
        transaction_paths = package_paths
        if effective_uid != 0:
            root_payload = create_root_package_stage(
                parent=staging_parent,
                asset=asset,
                archive_path=archive_path,
                archive=archive,
                package_paths=package_paths,
                runner=transaction_runner,
            )
            transaction_paths = root_payload.packages
        refresh_apt_metadata(runner=transaction_runner)
        simulate_install(
            transaction_paths,
            release.version,
            runner=transaction_runner,
        )
        pre_purge = recheck_confirmed_inventory(
            confirmed_inventory,
            runner=transaction_runner,
        )
        mutation_started = True
        purge_xpra_packages(pre_purge, runner=transaction_runner)
        install_local_packages(transaction_paths, runner=transaction_runner)
        installed = verify_installed_system(
            release.version,
            target.distro,
            runner=transaction_runner,
        )
        verify_release_is_current(release, selected_api)
        result = {
            "architecture": target.architecture,
            "asset_id": asset.asset_id,
            "asset_name": asset.name,
            "asset_sha256": asset.sha256,
            "commit": release.commit,
            "distro": target.distro,
            "installed_packages": [package.package for package in installed],
            "pre_purge_packages": [package.binary for package in pre_purge],
            "release_id": release.release_id,
            "schema": 1,
            "tag": release.tag,
            "version": release.version,
        }
    except Exception as error:
        if mutation_started:
            diagnostic_value = canonical_json(
                {
                    "asset": asset.name,
                    "distro": target.distro,
                    "pre_purge_packages": [package.binary for package in pre_purge],
                    "release": release.descriptor(),
                    "schema": 1,
                    "status": "failed",
                }
            )
            diagnostic = stage / "FAILED-TRANSACTION.json"
            diagnostic.write_text(diagnostic_value, encoding="utf-8")
            diagnostic.chmod(0o600)
            recovery_stage = stage
            if root_payload is not None:
                recovery_stage = root_payload.directory
                try:
                    encoded = diagnostic_value.encode("utf-8")
                    _install_root_file(
                        diagnostic,
                        recovery_stage / diagnostic.name,
                        size=len(encoded),
                        sha256=hashlib.sha256(encoded).hexdigest(),
                        runner=transaction_runner,
                    )
                except ReleaseError:
                    pass
                shutil.rmtree(stage, ignore_errors=True)
            if isinstance(error, ReleaseError):
                raise ReleaseError(
                    f"{error}; verified recovery payload preserved at {recovery_stage}"
                ) from error
            raise ReleaseError(
                "package transaction failed; verified recovery payload preserved "
                f"at {recovery_stage}"
            ) from error
        if root_payload is not None:
            remove_root_stage(
                root_payload.directory,
                parent=staging_parent,
                runner=transaction_runner,
            )
        shutil.rmtree(stage, ignore_errors=True)
        if isinstance(error, ReleaseError):
            raise
        raise ReleaseError("installer preflight failed") from error
    try:
        if root_payload is not None:
            remove_root_stage(
                root_payload.directory,
                parent=staging_parent,
                runner=transaction_runner,
            )
    finally:
        shutil.rmtree(stage)
    return result


def release_from_environment() -> ForkRelease:
    """Resolve the public release using an optional non-logged API token."""
    token = os.environ.get("GITHUB_TOKEN") or None
    return resolve_latest_release(GitHubApi(token=token))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command")
    value.set_defaults(command="install")
    resolve = commands.add_parser("resolve", help="print the newest release descriptor")
    resolve.add_argument("--asset-for", choices=tuple(sorted(ASSET_FOR_DISTRO)))
    commands.add_parser(
        "install", help="purge existing Xpra packages and install the newest release"
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "resolve":
            release = release_from_environment()
            descriptor = release.descriptor()
            if arguments.asset_for is not None:
                asset_name = ASSET_FOR_DISTRO[arguments.asset_for]
                descriptor["selected_asset"] = asset_name
            result = descriptor
        else:
            result = install_latest_release()
        sys.stdout.write(canonical_json(result))
    except InstallationCancelled as error:
        print(f"xpra release: {error}", file=sys.stderr)
        return 1
    except ReleaseError as error:
        print(f"xpra release: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
