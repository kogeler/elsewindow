"""Automatic remote Xpra live-test application and orchestration."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import tempfile
from pathlib import Path
from types import FrameType

from tools import container_payload

from .connection import (
    prepare_connection_files,
    provision_client,
    target_log,
    verify_ssh_settings,
)
from .keys import preflight, prepare_key
from .process import (
    LIVE_TARGET_USER_NAMESPACE,
    Arguments,
    KeyMaterial,
    LiveFailure,
    LiveResources,
    SignalExit,
    file_digest,
    file_metadata,
    parse_policy,
    podman_exec,
)
from .topology import create_internal_network, provision_target
from .xpra import LIVE_CASES, run_xpra_matrix


def parse_arguments(argv: list[str] | None = None) -> Arguments:
    parser = argparse.ArgumentParser(
        description="Run the automatic Xpra lifecycle matrix in disposable containers."
    )
    parser.add_argument("--target-image", required=True)
    parser.add_argument("--client-image", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--from-case",
        choices=LIVE_CASES,
        help="resume after a fixed failure; finish with one complete run",
    )
    parsed = parser.parse_args(argv)
    return Arguments(
        target_image=parsed.target_image,
        client_image=parsed.client_image,
        preflight_only=parsed.preflight_only,
        from_case=parsed.from_case,
    )


def verify_key_postconditions(
    key: KeyMaterial,
    private_before: tuple[int, ...],
    public_before: str,
) -> None:
    if file_metadata(key.identity_file) != private_before:
        raise LiveFailure("identity file metadata changed during the test")
    if file_digest(key.public_key) != public_before:
        raise LiveFailure("public key changed during the test")


def run_live(arguments: Arguments, key: KeyMaterial, resources: LiveResources) -> None:
    target_policy = parse_policy(
        "ELSEWINDOW_LIVE_TARGET_CONFINE",
        user_namespace=LIVE_TARGET_USER_NAMESPACE,
    )
    client_policy = parse_policy("ELSEWINDOW_LIVE_CLIENT_CONFINE")
    test_directory = tempfile.TemporaryDirectory(
        prefix="elsewindow-e2e.", dir=os.environ.get("TMPDIR")
    )
    test_dir = Path(test_directory.name)
    private_before = file_metadata(key.identity_file)
    public_before = file_digest(key.public_key)
    target: str | None = None
    try:
        network = create_internal_network(resources)
        target = provision_target(
            resources,
            arguments.target_image,
            target_policy,
            key.public_key,
            public_before,
            network,
            22,
        )
        connection = prepare_connection_files(resources, target, test_dir)
        client = provision_client(
            resources,
            arguments.client_image,
            client_policy,
            network,
            connection,
            key,
            test_dir,
        )
        verify_ssh_settings(resources, connection, client)
        run_xpra_matrix(resources, target, client, arguments.from_case)
        verify_key_postconditions(key, private_before, public_before)
        completion = (
            "complete"
            if arguments.from_case is None
            else f"tail from {arguments.from_case} passed; run it once completely"
        )
        print(
            f"live: automatic Xpra matrix {completion}; "
            f"host key {connection.observed_host_key}; ephemeral key unchanged",
            file=sys.stderr,
        )
    except Exception:
        if target is not None:
            print(
                f"live target log:\n{target_log(resources, target)[-8192:]}",
                file=sys.stderr,
            )
            print(
                podman_exec(
                    resources,
                    target,
                    "journalctl",
                    "--no-pager",
                    "--output=cat",
                    "-n",
                    "80",
                    purpose="reading target service diagnostics",
                ).decode(errors="replace")[-8192:],
                file=sys.stderr,
            )
        raise
    finally:
        clean = resources.cleanup()
        test_directory.cleanup()
        if not clean:
            raise LiveFailure("cleanup could not remove every owned Podman resource")


def install_signal_handlers() -> None:
    def stop(exit_code: int):
        def handler(_signal: int, _frame: FrameType | None) -> None:
            raise SignalExit(exit_code)

        return handler

    signal.signal(signal.SIGHUP, stop(129))
    signal.signal(signal.SIGINT, stop(130))
    signal.signal(signal.SIGTERM, stop(143))


def main(argv: list[str] | None = None) -> int:
    install_signal_handlers()
    resources = LiveResources(podman=os.environ.get("PODMAN", "podman"))
    key: KeyMaterial | None = None
    exit_code = 0
    try:
        arguments = parse_arguments(argv)
        key = prepare_key()
        preflight(arguments, key, resources)
        if not arguments.preflight_only:
            run_live(arguments, key, resources)
    except SystemExit as error:
        exit_code = int(error.code) if isinstance(error.code, int) else 1
    except SignalExit as error:
        exit_code = error.exit_code
    except (LiveFailure, OSError, container_payload.PayloadError) as error:
        print(f"live: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        if (
            (resources.target_name or resources.client_name or resources.network_name)
            and not resources.cleanup()
            and exit_code == 0
        ):
            exit_code = 1
        if key is not None:
            key.temporary.cleanup()
        print(f"live: cleanup complete (status {exit_code})", file=sys.stderr)
    return exit_code
