from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Self

import pytest

from tools import install_xpra_release as releases

COMMIT = "1" * 40
VERSION = "7.0-r1-1"
DIGEST = "2" * 64


def package_manifest(content: bytes = b"deb-package") -> dict[str, Any]:
    filename = f"xpra_{VERSION}_amd64.deb"
    return {
        "architecture": "amd64",
        "base_image_id": "base",
        "base_version": "13",
        "builder_image_id": "builder",
        "builder_image_input_sha256": DIGEST,
        "checkout_commit": COMMIT,
        "debian_version": VERSION,
        "distro": "debian-13",
        "packages": [
            {
                "architecture": "amd64",
                "name": filename,
                "package": "xpra",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "version": VERSION,
            }
        ],
        "revision": 1,
        "revision_first_parent_count": 1,
        "schema": 2,
        "selection": {},
        "selection_cache_sha256": DIGEST,
        "selection_resolution_sha256": DIGEST,
        "selection_sha256": DIGEST,
        "source_commit": COMMIT,
        "source_ref": "develop",
        "source_ref_commit": COMMIT,
        "workflow_sha256": DIGEST,
    }


def package_tar(
    tmp_path: Path,
    *,
    content: bytes = b"deb-package",
    manifest_content: bytes | None = None,
    package_version: str | None = None,
    extra_member: tuple[str, bytes] | None = None,
) -> bytes:
    manifest = package_manifest(
        content if manifest_content is None else manifest_content
    )
    if package_version is not None:
        manifest["packages"][0]["version"] = package_version
    filename = manifest["packages"][0]["name"]
    sums = f"{manifest['packages'][0]['sha256']}  {filename}\n".encode()
    values = {
        "SHA256SUMS": sums,
        "manifest.json": releases.canonical_json(manifest).encode(),
        filename: content,
    }
    if extra_member is not None:
        values[extra_member[0]] = extra_member[1]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, value in values.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return stream.getvalue()


def release_value(
    *,
    archive: bytes,
    release_id: int = 10,
    published_at: str = "2026-08-29T12:00:00Z",
    run_id: int = 123,
) -> dict[str, Any]:
    other = b"ubuntu"
    payloads = {
        releases.ASSET_NAMES[0]: archive,
        releases.ASSET_NAMES[1]: other,
    }
    attempt = 1
    transaction = {
        "assets": {
            name: {
                "digest": f"sha256:{hashlib.sha256(value).hexdigest()}",
                "size": len(value),
            }
            for name, value in payloads.items()
        },
        "attempt": attempt,
        "commit": COMMIT,
        "owner": releases.TRANSACTION_OWNER,
        "repository": releases.REPOSITORY,
        "run_id": run_id,
        "schema": 1,
        "version": VERSION,
        "workflow": releases.WORKFLOW,
    }
    tag = f"kogeler-deb-{VERSION}-run{run_id}-attempt{attempt}"
    return {
        "assets": [
            {
                "browser_download_url": (
                    f"https://github.com/{releases.REPOSITORY}/releases/download/"
                    f"{tag}/{name}"
                ),
                "digest": metadata["digest"],
                "id": index + 100,
                "name": name,
                "size": metadata["size"],
            }
            for index, (name, metadata) in enumerate(transaction["assets"].items())
        ],
        "body": (
            "Packages.\n\n"
            f"{releases.TRANSACTION_PREFIX}"
            f"{json.dumps(transaction, sort_keys=True)} -->"
        ),
        "draft": False,
        "id": release_id,
        "name": VERSION,
        "prerelease": False,
        "published_at": published_at,
        "tag_name": tag,
        "target_commitish": COMMIT,
    }


class StubApi:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.verified: list[str] = []

    def releases(self) -> tuple[Any, ...]:
        return tuple(self.values)

    def verify_develop_commit(self, commit: str) -> None:
        self.verified.append(commit)


def write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def test_resolve_selects_newest_owned_release(tmp_path: Path) -> None:
    archive = package_tar(tmp_path)
    older = release_value(
        archive=archive,
        release_id=9,
        published_at="2026-08-28T12:00:00Z",
        run_id=122,
    )
    newer = release_value(archive=archive)
    unrelated = {
        "body": "ordinary upstream release",
        "id": 11,
        "tag_name": "unrelated",
    }
    api = StubApi([older, unrelated, newer])

    selected = releases.resolve_latest_release(api)  # type: ignore[arg-type]

    assert selected.release_id == 10
    assert selected.commit == COMMIT
    assert tuple(asset.name for asset in selected.assets) == tuple(
        sorted(releases.ASSET_NAMES)
    )
    assert api.verified == [COMMIT]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(draft=True), "ordinary release"),
        (
            lambda value: value["assets"][0].update(digest=f"sha256:{'f' * 64}"),
            "digest differs",
        ),
        (
            lambda value: value.update(target_commitish="f" * 40),
            "target does not match",
        ),
        (
            lambda value: value.update(body=value["body"] + value["body"]),
            "ambiguous",
        ),
    ],
)
def test_owned_release_metadata_fails_closed(
    tmp_path: Path, mutation: Any, message: str
) -> None:
    value = release_value(archive=package_tar(tmp_path))
    mutation(value)

    with pytest.raises(releases.ReleaseError, match=message):
        releases.parse_release(value)


def test_duplicate_release_identity_is_rejected(tmp_path: Path) -> None:
    value = release_value(archive=package_tar(tmp_path))
    duplicate = copy.deepcopy(value)

    with pytest.raises(releases.ReleaseError, match="duplicate identities"):
        releases.resolve_latest_release(StubApi([value, duplicate]))  # type: ignore[arg-type]


def test_empty_release_collection_is_rejected() -> None:
    with pytest.raises(releases.ReleaseError, match="no canonical"):
        releases.resolve_latest_release(StubApi([]))  # type: ignore[arg-type]


def test_missing_release_asset_is_rejected(tmp_path: Path) -> None:
    value = release_value(archive=package_tar(tmp_path))
    value["assets"].pop()

    with pytest.raises(releases.ReleaseError, match="wrong asset set"):
        releases.parse_release(value)


def test_fresh_resolution_detects_release_race(tmp_path: Path) -> None:
    content = package_tar(tmp_path)
    old_value = release_value(
        archive=content,
        release_id=9,
        published_at="2026-08-28T12:00:00Z",
        run_id=122,
    )
    selected = releases.resolve_latest_release(StubApi([old_value]))  # type: ignore[arg-type]
    new_value = release_value(archive=content)

    with pytest.raises(releases.ReleaseError, match="newer or changed"):
        releases.verify_release_is_current(  # type: ignore[arg-type]
            selected, StubApi([old_value, new_value])
        )


def test_image_release_labels_reject_stale_cache(tmp_path: Path) -> None:
    value = release_value(archive=package_tar(tmp_path))
    parsed = releases.parse_release(value)
    assert parsed is not None
    selected, _published = parsed
    asset = selected.asset(releases.ASSET_NAMES[0])
    labels = releases.image_release_labels(selected, asset, "debian-13")
    releases.verify_image_release_labels(labels, selected, asset, "debian-13")
    labels[f"{releases.IMAGE_LABEL_PREFIX}.commit"] = "f" * 40

    with pytest.raises(releases.ReleaseError, match="label differs"):
        releases.verify_image_release_labels(labels, selected, asset, "debian-13")


def test_verify_develop_commit_requires_selected_merge_base() -> None:
    api = releases.GitHubApi()
    api.get_json = lambda _url: {  # type: ignore[method-assign]
        "merge_base_commit": {"sha": "f" * 40},
        "status": "ahead",
    }

    with pytest.raises(releases.ReleaseError, match="not in current develop"):
        api.verify_develop_commit(COMMIT)


def test_validate_package_archive(tmp_path: Path) -> None:
    content = package_tar(tmp_path)
    value = release_value(archive=content)
    parsed = releases.parse_release(value)
    assert parsed is not None
    selected, _published = parsed
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    write_private(path, content)

    validated = releases.validate_package_archive(
        path, release=selected, asset=asset, distro="debian-13"
    )

    assert validated.version == VERSION
    assert validated.checkout_commit == COMMIT
    assert [package.package for package in validated.packages] == ["xpra"]


def test_archive_rejects_unexpected_member(tmp_path: Path) -> None:
    content = package_tar(tmp_path, extra_member=("unexpected", b"data"))
    value = release_value(archive=content)
    selected, _published = releases.parse_release(value)  # type: ignore[misc]
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    write_private(path, content)

    with pytest.raises(releases.ReleaseError, match="unexpected member"):
        releases.validate_package_archive(
            path, release=selected, asset=asset, distro="debian-13"
        )


def test_archive_rejects_package_byte_mismatch(tmp_path: Path) -> None:
    content = package_tar(tmp_path, content=b"actual", manifest_content=b"expected")
    value = release_value(archive=content)
    selected, _published = releases.parse_release(value)  # type: ignore[misc]
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    write_private(path, content)

    with pytest.raises(releases.ReleaseError, match="package bytes differ"):
        releases.validate_package_archive(
            path, release=selected, asset=asset, distro="debian-13"
        )


def test_archive_rejects_mixed_package_version(tmp_path: Path) -> None:
    content = package_tar(tmp_path, package_version="other-version")
    value = release_value(archive=content)
    parsed = releases.parse_release(value)
    assert parsed is not None
    selected, _published = parsed
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    write_private(path, content)

    with pytest.raises(releases.ReleaseError, match="mixed version"):
        releases.validate_package_archive(
            path, release=selected, asset=asset, distro="debian-13"
        )


def test_archive_file_must_be_private(tmp_path: Path) -> None:
    content = package_tar(tmp_path)
    value = release_value(archive=content)
    selected, _published = releases.parse_release(value)  # type: ignore[misc]
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    path.write_bytes(content)
    path.chmod(0o644)

    with pytest.raises(releases.ReleaseError, match="identity or size"):
        releases.validate_package_archive(
            path, release=selected, asset=asset, distro="debian-13"
        )


class Response(io.BytesIO):
    def __init__(self, value: bytes, url: str) -> None:
        super().__init__(value)
        self.headers = {"Content-Length": str(len(value))}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_arguments: object) -> None:
        self.close()


class Opener:
    def __init__(self, value: bytes, url: str) -> None:
        self.value = value
        self.url = url

    def open(self, _request: object, *, timeout: float) -> Response:
        assert timeout == releases.HTTP_TIMEOUT
        return Response(self.value, self.url)


def test_download_asset_creates_verified_private_file(tmp_path: Path) -> None:
    value = b"release-asset"
    asset = releases.ReleaseAsset(
        1,
        releases.ASSET_NAMES[0],
        len(value),
        hashlib.sha256(value).hexdigest(),
        "https://github.com/kogeler/xpra/releases/download/tag/asset",
    )
    destination = tmp_path / "asset.tar"

    releases.download_asset(  # type: ignore[arg-type]
        asset,
        destination,
        opener=Opener(
            value,
            "https://release-assets.githubusercontent.com/github-production-release-asset",
        ),
    )

    assert destination.read_bytes() == value
    assert destination.stat().st_mode & 0o777 == 0o600


def test_download_failure_removes_partial_file(tmp_path: Path) -> None:
    value = b"release-asset"
    asset = releases.ReleaseAsset(
        1,
        releases.ASSET_NAMES[0],
        len(value) + 1,
        hashlib.sha256(value).hexdigest(),
        "https://github.com/kogeler/xpra/releases/download/tag/asset",
    )
    destination = tmp_path / "asset.tar"

    with pytest.raises(releases.ReleaseError, match="Content-Length"):
        releases.download_asset(  # type: ignore[arg-type]
            asset,
            destination,
            opener=Opener(
                value,
                "https://release-assets.githubusercontent.com/github-production-release-asset",
            ),
        )

    assert not destination.exists()


def test_verify_file_rejects_hard_link(tmp_path: Path) -> None:
    value = b"archive"
    path = tmp_path / "archive.tar"
    write_private(path, value)
    os.link(path, tmp_path / "other.tar")

    with pytest.raises(releases.ReleaseError, match="identity or size"):
        releases.verify_file(
            path, size=len(value), sha256=hashlib.sha256(value).hexdigest()
        )


def command_result(
    arguments: list[str] | tuple[str, ...],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


def test_privileged_commands_use_the_fixed_sudo_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        return command_result(tuple(arguments), stdout="ok\n")

    monkeypatch.setattr(releases, "run_command", runner)

    completed = releases.run_sudo_command((releases.APT_GET, "update"))

    assert completed.stdout == "ok\n"
    assert calls == [
        (
            releases.SUDO,
            "--",
            releases.ENV,
            "DEBIAN_FRONTEND=noninteractive",
            "LANG=C.UTF-8",
            "LC_ALL=C.UTF-8",
            releases.APT_GET,
            "update",
        )
    ]


@pytest.mark.parametrize(
    "value",
    (
        "relative",
        "/var/tmp/outside",
        "/var/tmp/xpra-release-install.short",
        "/var/tmp/xpra-release-install.0123456789\nextra",
    ),
)
def test_root_staging_path_is_exact(value: str) -> None:
    with pytest.raises(releases.ReleaseError, match="root staging directory"):
        releases._root_stage_path(value, Path("/var/tmp"))

    assert releases._root_stage_path(
        "/var/tmp/xpra-release-install.0123456789\n", Path("/var/tmp")
    ) == Path("/var/tmp/xpra-release-install.0123456789")


def test_detect_system_rejects_unreadable_state_before_command_or_network(
    tmp_path: Path,
) -> None:
    called = False

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return command_result(tuple(arguments))

    with pytest.raises(releases.ReleaseError, match="cannot read /etc/os-release"):
        releases.detect_system(
            os_release_path=tmp_path / "missing",
            runner=runner,
        )
    assert called is False


@pytest.mark.parametrize(
    ("content", "distro"),
    [
        ('ID=debian\nVERSION_ID="13"\n', "debian-13"),
        ('ID="ubuntu"\nVERSION_ID=26.04\n', "ubuntu-26.04"),
    ],
)
def test_detect_system_accepts_only_exact_supported_os(
    tmp_path: Path, content: str, distro: str
) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(content, encoding="utf-8")

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        assert tuple(arguments) == (releases.DPKG, "--print-architecture")
        return command_result(tuple(arguments), stdout="amd64\n")

    target = releases.detect_system(
        os_release_path=os_release,
        runner=runner,
    )

    assert target.distro == distro
    assert target.architecture == "amd64"


def test_detect_system_rejects_derivative_and_architecture(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=debian\nVERSION_ID=12\n", encoding="utf-8")

    with pytest.raises(releases.ReleaseError, match="only Debian 13"):
        releases.detect_system(
            os_release_path=os_release,
            runner=lambda arguments: command_result(tuple(arguments), stdout="amd64\n"),
        )

    os_release.write_text("ID=debian\nVERSION_ID=13\n", encoding="utf-8")
    with pytest.raises(releases.ReleaseError, match="only amd64"):
        releases.detect_system(
            os_release_path=os_release,
            runner=lambda arguments: command_result(tuple(arguments), stdout="arm64\n"),
        )


def test_detect_system_accepts_standard_os_release_symlink(tmp_path: Path) -> None:
    canonical = tmp_path / "usr/lib/os-release"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("ID=debian\nVERSION_ID=13\n", encoding="utf-8")
    link = tmp_path / "etc/os-release"
    link.parent.mkdir()
    link.symlink_to(canonical)

    target = releases.detect_system(
        os_release_path=link,
        runner=lambda arguments: command_result(tuple(arguments), stdout="amd64\n"),
    )

    assert target.distro == "debian-13"


def test_declined_existing_inventory_stops_before_release_network(
    tmp_path: Path,
) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=debian\nVERSION_ID=13\n", encoding="utf-8")
    inventory = f"xpra:amd64\txpra\txpra\tii \t{VERSION}\n"
    confirmed: list[tuple[releases.InstalledPackage, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        if tuple(arguments) == (releases.DPKG, "--print-architecture"):
            return command_result(tuple(arguments), stdout="amd64\n")
        if arguments[0] == releases.DPKG_QUERY:
            return command_result(tuple(arguments), stdout=inventory)
        raise AssertionError(f"unexpected command before confirmation: {arguments}")

    class NoNetworkApi:
        def releases(self) -> tuple[Any, ...]:
            raise AssertionError("release API must not run after declined confirmation")

    def decline(packages: tuple[releases.InstalledPackage, ...]) -> bool:
        confirmed.append(packages)
        return False

    with pytest.raises(releases.InstallationCancelled, match="no changes"):
        releases.install_latest_release(
            confirmation=decline,
            runner=runner,
            api=NoNetworkApi(),  # type: ignore[arg-type]
            os_release_path=os_release,
            euid=1000,
            staging_parent=tmp_path,
        )

    assert [[package.binary for package in packages] for packages in confirmed] == [
        ["xpra:amd64"]
    ]


def test_required_package_set_is_exact() -> None:
    packages = tuple(
        releases.PackageRecord(
            f"{name}_{VERSION}_amd64.deb", name, VERSION, "amd64", 1, DIGEST
        )
        for name in releases.REQUIRED_XPRA_PACKAGES
    )
    archive = releases.PackageArchive("debian-13", VERSION, COMMIT, packages, {})
    selected = releases.select_required_packages(archive)

    assert tuple(package.package for package in selected.packages) == (
        releases.REQUIRED_XPRA_PACKAGES
    )

    with pytest.raises(releases.ReleaseError, match="lacks required packages"):
        releases.select_required_packages(
            releases.PackageArchive("debian-13", VERSION, COMMIT, packages[:-1], {})
        )


def test_extract_package_files_reverifies_bytes(tmp_path: Path) -> None:
    content = package_tar(tmp_path)
    value = release_value(archive=content)
    selected, _published = releases.parse_release(value)  # type: ignore[misc]
    asset = selected.asset(releases.ASSET_NAMES[0])
    path = tmp_path / asset.name
    write_private(path, content)
    archive = releases.validate_package_archive(
        path, release=selected, asset=asset, distro="debian-13"
    )

    extracted = releases.extract_package_files(path, archive, tmp_path / "packages")

    assert [item.name for item in extracted] == [f"xpra_{VERSION}_amd64.deb"]
    assert extracted[0].read_bytes() == b"deb-package"
    assert extracted[0].stat().st_mode & 0o777 == 0o600


def test_verify_deb_metadata_uses_actual_control_fields(tmp_path: Path) -> None:
    path = tmp_path / f"xpra_{VERSION}_amd64.deb"
    write_private(path, b"deb")
    record = releases.PackageRecord(
        path.name, "xpra", VERSION, "amd64", 3, hashlib.sha256(b"deb").hexdigest()
    )
    archive = releases.PackageArchive("debian-13", VERSION, COMMIT, (record,), {})
    values = {"Package": "xpra", "Version": VERSION, "Architecture": "amd64"}

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        assert arguments[0] == releases.DPKG_DEB
        if arguments[1] == "--contents":
            return command_result(
                tuple(arguments),
                stdout=(
                    "-rw-r--r-- root/root 1 2026-08-30 00:00 "
                    "./usr/share/doc/xpra/copyright\n"
                ),
            )
        assert arguments[1] == "--field"
        return command_result(tuple(arguments), stdout=f"{values[arguments[-1]]}\n")

    releases.verify_deb_metadata(archive, (path,), runner=runner)

    values["Version"] = "wrong"
    with pytest.raises(releases.ReleaseError, match="version differs"):
        releases.verify_deb_metadata(archive, (path,), runner=runner)


def test_verify_deb_metadata_requires_actual_native_codec_members(
    tmp_path: Path,
) -> None:
    path = tmp_path / f"xpra-codecs_{VERSION}_amd64.deb"
    write_private(path, b"deb")
    record = releases.PackageRecord(
        path.name,
        "xpra-codecs",
        VERSION,
        "amd64",
        3,
        hashlib.sha256(b"deb").hexdigest(),
    )
    archive = releases.PackageArchive("debian-13", VERSION, COMMIT, (record,), {})
    fields = {
        "Package": "xpra-codecs",
        "Version": VERSION,
        "Architecture": "amd64",
    }
    members = [
        "usr/lib/python3/dist-packages/xpra/codecs/libva/__init__.py",
        (
            "usr/lib/python3/dist-packages/xpra/codecs/libva/"
            "encoder.cpython-313-x86_64-linux-gnu.so"
        ),
        (
            "usr/lib/python3/dist-packages/xpra/codecs/libva/"
            "decoder.cpython-313-x86_64-linux-gnu.so"
        ),
        (
            "usr/lib/python3/dist-packages/xpra/codecs/libyuv/"
            "converter.cpython-313-x86_64-linux-gnu.so"
        ),
    ]

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        if arguments[1] == "--field":
            return command_result(
                tuple(arguments),
                stdout=f"{fields[arguments[-1]]}\n",
            )
        assert arguments[1] == "--contents"
        output = "".join(
            f"-rw-r--r-- root/root 1 2026-08-30 00:00 ./{member}\n"
            for member in members
        )
        return command_result(tuple(arguments), stdout=output)

    releases.verify_deb_metadata(archive, (path,), runner=runner)

    decoder = members[2]
    members[2] = f"SYMLINK {decoder}"

    def symlink_runner(
        arguments: releases.Sequence[str],
    ) -> subprocess.CompletedProcess[str]:
        if arguments[1] == "--field":
            return command_result(
                tuple(arguments),
                stdout=f"{fields[arguments[-1]]}\n",
            )
        assert arguments[1] == "--contents"
        output = "".join(
            (
                f"lrwxrwxrwx root/root 0 2026-08-30 00:00 "
                f"./{member.removeprefix('SYMLINK ')} -> /tmp/decoder.so\n"
                if member.startswith("SYMLINK ")
                else f"-rw-r--r-- root/root 1 2026-08-30 00:00 ./{member}\n"
            )
            for member in members
        )
        return command_result(tuple(arguments), stdout=output)

    with pytest.raises(releases.ReleaseError, match="required runtime file"):
        releases.verify_deb_metadata(archive, (path,), runner=symlink_runner)

    members[2] = decoder
    members.append(
        "usr/lib/python3/dist-packages/xpra/codecs/libva/"
        "encoder.cpython-314-x86_64-linux-gnu.so"
    )
    with pytest.raises(releases.ReleaseError, match="required runtime file"):
        releases.verify_deb_metadata(archive, (path,), runner=runner)


def test_deb_content_listing_rejects_unsafe_or_duplicate_paths(tmp_path: Path) -> None:
    path = tmp_path / "package.deb"
    write_private(path, b"deb")
    lines = [
        "-rw-r--r-- root/root 1 2026-08-30 00:00 ./usr/bin/xpra\n",
        "-rw-r--r-- root/root 1 2026-08-30 00:00 ./../escape\n",
    ]

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        return command_result(tuple(arguments), stdout="".join(lines))

    with pytest.raises(releases.ReleaseError, match="content line 2 is invalid"):
        releases._deb_regular_files(path, runner)

    lines[1] = lines[0]
    with pytest.raises(releases.ReleaseError, match="content line 2 is invalid"):
        releases._deb_regular_files(path, runner)


def test_verify_deb_metadata_requires_wayland_server_only_in_ubuntu(
    tmp_path: Path,
) -> None:
    path = tmp_path / f"xpra-server-wayland_{VERSION}_amd64.deb"
    write_private(path, b"deb")
    record = releases.PackageRecord(
        path.name,
        "xpra-server-wayland",
        VERSION,
        "amd64",
        3,
        hashlib.sha256(b"deb").hexdigest(),
    )
    fields = {
        "Package": "xpra-server-wayland",
        "Version": VERSION,
        "Architecture": "amd64",
    }

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        if arguments[1] == "--field":
            return command_result(
                tuple(arguments),
                stdout=f"{fields[arguments[-1]]}\n",
            )
        assert arguments[1] == "--contents"
        return command_result(
            tuple(arguments),
            stdout=(
                "-rw-r--r-- root/root 1 2026-08-30 00:00 "
                "./usr/share/doc/xpra-server-wayland/copyright\n"
            ),
        )

    debian = releases.PackageArchive("debian-13", VERSION, COMMIT, (record,), {})
    releases.verify_deb_metadata(debian, (path,), runner=runner)

    ubuntu = releases.PackageArchive("ubuntu-26.04", VERSION, COMMIT, (record,), {})
    with pytest.raises(releases.ReleaseError, match="required runtime file"):
        releases.verify_deb_metadata(ubuntu, (path,), runner=runner)


def test_xpra_inventory_uses_namespace_and_source_metadata() -> None:
    output = "".join(
        (
            "ordinary:amd64\tordinary\tordinary\tii \t1\n",
            f"xpra:amd64\txpra\txpra\tii \t{VERSION}\n",
            f"xpra-client:amd64\txpra-client\txpra\trc \t{VERSION}\n",
            f"python3-xpra-helper:amd64\tpython3-xpra-helper\txpra\tii \t{VERSION}\n",
        )
    )

    packages = releases.list_xpra_packages(
        runner=lambda arguments: command_result(tuple(arguments), stdout=output)
    )

    assert [package.package for package in packages] == [
        "python3-xpra-helper",
        "xpra",
        "xpra-client",
    ]
    assert packages[-1].status == "rc "


def test_existing_xpra_inventory_requires_an_explicit_interactive_yes() -> None:
    packages = (
        releases.InstalledPackage("xpra:amd64", "xpra", "xpra", "ii ", VERSION),
        releases.InstalledPackage(
            "xpra-client:amd64", "xpra-client", "xpra", "rc ", VERSION
        ),
    )
    rejected_output = io.StringIO()

    assert not releases.confirm_xpra_replacement(
        packages,
        input_stream=io.StringIO("n\n"),
        output_stream=rejected_output,
    )
    prompt = rejected_output.getvalue()
    assert "xpra:amd64" in prompt
    assert "xpra-client:amd64" in prompt
    assert VERSION in prompt
    assert "dpkg status: ii" in prompt
    assert "dpkg status: rc" in prompt
    assert prompt.endswith("[y/N]: ")

    assert releases.confirm_xpra_replacement(
        packages,
        input_stream=io.StringIO("yes\n"),
        output_stream=io.StringIO(),
    )
    assert not releases.confirm_xpra_replacement(
        packages,
        input_stream=io.StringIO("\n"),
        output_stream=io.StringIO(),
    )


def test_default_confirmation_uses_the_controlling_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = releases.InstalledPackage("xpra:amd64", "xpra", "xpra", "ii ", VERSION)

    class TerminalInput:
        def __init__(self) -> None:
            self.closed = False

        @staticmethod
        def readline() -> str:
            return "yes\n"

        def close(self) -> None:
            self.closed = True

    class TerminalOutput:
        def __init__(self) -> None:
            self.output = io.StringIO()
            self.closed = False

        def write(self, value: str) -> int:
            return self.output.write(value)

        def flush(self) -> None:
            self.output.flush()

        def close(self) -> None:
            self.closed = True

    terminal_input = TerminalInput()
    terminal_output = TerminalOutput()
    calls: list[tuple[object, ...]] = []

    def open_terminal(
        path: str, mode: str, **options: object
    ) -> TerminalInput | TerminalOutput:
        calls.append((path, mode, options))
        if mode == "r":
            return terminal_input
        if mode == "w":
            return terminal_output
        raise AssertionError(f"unexpected terminal mode: {mode}")

    monkeypatch.setattr("builtins.open", open_terminal)

    assert releases.confirm_xpra_replacement((package,))
    assert calls == [
        (
            "/dev/tty",
            "r",
            {"encoding": "utf-8", "errors": "strict"},
        ),
        (
            "/dev/tty",
            "w",
            {"encoding": "utf-8", "errors": "strict", "buffering": 1},
        ),
    ]
    assert terminal_input.closed
    assert terminal_output.closed
    assert "xpra:amd64" in terminal_output.output.getvalue()
    assert terminal_output.output.getvalue().endswith("[y/N]: ")


def test_default_confirmation_falls_back_without_a_controlling_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = releases.InstalledPackage("xpra:amd64", "xpra", "xpra", "ii ", VERSION)
    output = io.StringIO()

    def no_terminal(*_arguments: object, **_options: object) -> None:
        raise OSError("no controlling terminal")

    monkeypatch.setattr("builtins.open", no_terminal)
    monkeypatch.setattr(releases.sys, "stdin", io.StringIO("y\n"))
    monkeypatch.setattr(releases.sys, "stderr", output)

    assert releases.confirm_xpra_replacement((package,))
    assert "xpra:amd64" in output.getvalue()


def test_default_confirmation_closes_partial_terminal_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = releases.InstalledPackage("xpra:amd64", "xpra", "xpra", "ii ", VERSION)
    output = io.StringIO()

    class TerminalInput:
        closed = False

        def close(self) -> None:
            self.closed = True

    terminal_input = TerminalInput()

    def open_terminal(_path: str, mode: str, **_options: object) -> TerminalInput:
        if mode == "r":
            return terminal_input
        raise OSError("terminal output unavailable")

    monkeypatch.setattr("builtins.open", open_terminal)
    monkeypatch.setattr(releases.sys, "stdin", io.StringIO("yes\n"))
    monkeypatch.setattr(releases.sys, "stderr", output)

    assert releases.confirm_xpra_replacement((package,))
    assert terminal_input.closed
    assert output.getvalue().endswith("[y/N]: ")


def test_empty_xpra_inventory_needs_no_prompt() -> None:
    output = io.StringIO()

    assert releases.confirm_xpra_replacement(
        (), input_stream=io.StringIO(""), output_stream=output
    )
    assert output.getvalue() == ""


def test_confirmed_inventory_must_be_unchanged_before_mutation() -> None:
    original = releases.InstalledPackage("xpra:amd64", "xpra", "xpra", "ii ", VERSION)

    def inventory(package: str) -> str:
        return f"{package}:amd64\t{package}\txpra\tii \t{VERSION}\n"

    assert releases.recheck_confirmed_inventory(
        (original,),
        runner=lambda arguments: command_result(
            tuple(arguments), stdout=inventory("xpra")
        ),
    ) == (original,)

    with pytest.raises(releases.ReleaseError, match="changed after confirmation"):
        releases.recheck_confirmed_inventory(
            (original,),
            runner=lambda arguments: command_result(
                tuple(arguments), stdout=inventory("xpra-client")
            ),
        )


def test_simulation_requires_every_local_xpra_package(tmp_path: Path) -> None:
    paths = tuple(
        tmp_path / f"{name}_{VERSION}_amd64.deb"
        for name in releases.REQUIRED_XPRA_PACKAGES
    )
    output = "".join(
        (
            f"Inst {name} [older-version] ({VERSION} local-deb [amd64])\n"
            if index % 2
            else f"Inst {name} ({VERSION} local-deb [amd64])\n"
        )
        for index, name in enumerate(releases.REQUIRED_XPRA_PACKAGES)
    )
    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        return command_result(tuple(arguments), stdout=output)

    releases.simulate_install(paths, VERSION, runner=runner)

    assert calls[0][:6] == (
        releases.APT_GET,
        "--simulate",
        "--no-install-recommends",
        "--reinstall",
        "--allow-downgrades",
        "install",
    )
    assert calls[0][6:] == (
        *releases.REQUIRED_APT_PACKAGES,
        *(str(path) for path in paths),
    )
    assert releases.REQUIRED_APT_PACKAGES == (
        "libva-drm2",
        "python3-opengl",
        "python3-venv",
        "dbus-daemon",
        "python3-dbus",
        "python3-gi",
        "xdg-desktop-portal",
        "xdg-desktop-portal-gtk",
    )

    missing = output.replace(f"Inst xpra ({VERSION} local-deb [amd64])\n", "")
    with pytest.raises(releases.ReleaseError, match="exact local Xpra set"):
        releases.simulate_install(
            paths,
            VERSION,
            runner=lambda arguments: command_result(tuple(arguments), stdout=missing),
        )


def test_apt_metadata_refresh_uses_one_fixed_command() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        return command_result(tuple(arguments))

    releases.refresh_apt_metadata(runner=runner)

    assert calls == [(releases.APT_GET, "update")]


def test_local_deb_installation_uses_apt_for_dependency_resolution(
    tmp_path: Path,
) -> None:
    paths = tuple(
        tmp_path / f"{name}_{VERSION}_amd64.deb"
        for name in releases.REQUIRED_XPRA_PACKAGES
    )
    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        return command_result(tuple(arguments))

    releases.install_local_packages(paths, runner=runner)

    assert calls == [
        (
            releases.APT_GET,
            "install",
            "--yes",
            "--no-install-recommends",
            *releases.REQUIRED_APT_PACKAGES,
            *(str(path) for path in paths),
        )
    ]
    assert all(call[0] != releases.DPKG for call in calls)


def test_empty_purge_skips_apt_and_rechecks_inventory() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        return command_result(tuple(arguments), stdout="")

    releases.purge_xpra_packages((), runner=runner)

    assert calls == [
        (
            releases.DPKG_QUERY,
            "--show",
            (
                "--showformat=${binary:Package}\\t${Package}\\t"
                "${source:Package}\\t${db:Status-Abbrev}\\t${Version}\\n"
            ),
        )
    ]


def test_verify_installed_system_checks_exact_inventory_and_runtime(
    tmp_path: Path,
) -> None:
    xpra = tmp_path / "usr/bin/xpra"
    xpra.parent.mkdir(parents=True)
    xpra.write_text("#!/bin/sh\n", encoding="utf-8")
    css = tmp_path / "usr/share/xpra/css"
    css.mkdir(parents=True)
    (css / "base.css").write_text("* {}\n", encoding="utf-8")
    inventory = "".join(
        f"{name}:amd64\t{name}\txpra\tii \t{VERSION}\n"
        for name in releases.REQUIRED_XPRA_PACKAGES
    )

    calls: list[tuple[str, ...]] = []

    def runner(arguments: releases.Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(arguments))
        if arguments[0] == releases.DPKG_QUERY:
            return command_result(tuple(arguments), stdout=inventory)
        return command_result(tuple(arguments))

    packages = releases.verify_installed_system(
        VERSION,
        "debian-13",
        runner=runner,
        root=tmp_path,
    )

    assert (
        tuple(package.package for package in packages)
        == releases.REQUIRED_XPRA_PACKAGES
    )
    assert calls[-2] == (
        releases.PYTHON,
        "-c",
        "; ".join(f"import {module}" for module in releases.COMMON_RUNTIME_IMPORTS),
    )

    releases.verify_installed_system(
        VERSION,
        "ubuntu-26.04",
        runner=runner,
        root=tmp_path,
    )
    assert calls[-2] == (
        releases.PYTHON,
        "-c",
        "; ".join(
            f"import {module}"
            for module in (
                *releases.COMMON_RUNTIME_IMPORTS,
                *releases.DISTRO_RUNTIME_IMPORTS["ubuntu-26.04"],
            )
        ),
    )

    with pytest.raises(releases.ReleaseError, match="distribution is unsupported"):
        releases.verify_installed_system(
            VERSION,
            "unsupported",
            runner=runner,
            root=tmp_path,
        )


def test_cli_defaults_to_install_and_has_no_confirmation_bypass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    def install() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"schema": 1, "status": "installed"}

    monkeypatch.setattr(releases, "install_latest_release", install)

    assert releases.main([]) == 0
    assert calls == 1
    assert json.loads(capsys.readouterr().out) == {
        "schema": 1,
        "status": "installed",
    }
    with pytest.raises(SystemExit):
        releases.parser().parse_args(("install", "--yes-purge-existing-xpra"))


def test_cli_declined_confirmation_exits_without_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def cancelled() -> dict[str, Any]:
        raise releases.InstallationCancelled(
            "installation cancelled; no changes were made"
        )

    monkeypatch.setattr(releases, "install_latest_release", cancelled)

    assert releases.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cancelled; no changes were made" in captured.err
