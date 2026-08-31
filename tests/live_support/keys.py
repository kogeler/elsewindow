"""Ephemeral SSH key preparation and rootless Podman preflight."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

from .process import (
    LIVE_DRIVER,
    Arguments,
    KeyMaterial,
    LiveFailure,
    LiveResources,
    checked,
    key_fingerprint,
    podman_output,
    require_program,
    validate_image_reference,
)


def prepare_key() -> KeyMaterial:
    temporary = tempfile.TemporaryDirectory(
        prefix="elsewindow-live-key.", dir=os.environ.get("TMPDIR")
    )
    identity_file = Path(temporary.name) / "id_ed25519"
    checked(
        [
            require_program("ssh-keygen"),
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "elsewindow-ephemeral-live-test",
            "-f",
            str(identity_file),
        ],
        "generating the ephemeral SSH key",
    )
    public_key = Path(f"{identity_file}.pub")
    identity_file.chmod(0o600)
    public_key.chmod(0o600)

    if not public_key.is_file() or not os.access(public_key, os.R_OK):
        raise LiveFailure("generated public key is not readable")
    if not identity_file.is_file() or not os.access(identity_file, os.R_OK):
        raise LiveFailure("generated identity is not readable")
    if stat.S_IMODE(identity_file.stat().st_mode) & 0o077:
        raise LiveFailure("generated identity has unsafe permissions")
    fields = public_key.read_text(encoding="utf-8").split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise LiveFailure("generated public key is malformed")

    return KeyMaterial(
        public_key=public_key,
        identity_file=identity_file,
        fingerprint=key_fingerprint(public_key),
        temporary=temporary,
    )


def preflight(arguments: Arguments, key: KeyMaterial, resources: LiveResources) -> None:
    validate_image_reference(arguments.target_image, "target image")
    validate_image_reference(arguments.client_image, "client image")
    if not LIVE_DRIVER.is_file():
        raise LiveFailure("the Xpra live driver is missing")
    require_program("ssh-keygen")
    podman_output(resources, "info", purpose="querying Podman")
    rootless = podman_output(
        resources,
        "info",
        "--format",
        "{{.Host.Security.Rootless}}",
        purpose="querying Podman isolation",
    )
    if rootless != "true":
        raise LiveFailure(f"rootless Podman is required, got rootless={rootless}")
    for role, image in (
        ("target", arguments.target_image),
        ("client", arguments.client_image),
    ):
        checked(
            [resources.podman, "image", "exists", image],
            f"checking the {role} image",
        )
    print(
        "live: preflight passed "
        f"(automatic Ed25519 key, fingerprint {key.fingerprint})",
        file=sys.stderr,
    )
