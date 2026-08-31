"""Disposable Podman network and Xpra SSH-target provisioning."""

from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

from .process import (
    OWNER_LABEL,
    RUN_LABEL,
    LiveFailure,
    LiveResources,
    checked,
    podman_exec,
    podman_output,
    run_process,
)


def unique_name(prefix: str) -> str:
    return f"{prefix}-{os.getpid()}-{secrets.token_hex(4)}"


def create_internal_network(resources: LiveResources) -> str:
    name = unique_name("elsewindow-e2e-net")
    resources.network_name = name
    print("live: creating the private test network", file=sys.stderr)
    checked(
        [
            resources.podman,
            "network",
            "create",
            "--internal",
            "--label",
            f"{RUN_LABEL}={name}",
            "--label",
            f"{OWNER_LABEL}=live-network",
            name,
        ],
        "creating the private network",
    )
    internal = podman_output(
        resources,
        "network",
        "inspect",
        "--format",
        "{{.Internal}}",
        name,
        purpose="inspecting the private network",
    )
    if internal != "true":
        raise LiveFailure("the automatic live network is not internal")
    return name


def wait_for_sshd(resources: LiveResources, target: str, port: int) -> None:
    for _attempt in range(90):
        completed = run_process(
            [resources.podman, "exec", target, "ss", "-Hltn", f"sport = :{port}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0 and "LISTEN" in completed.stdout:
            return
        time.sleep(1)
    raise LiveFailure("the target SSH server did not start")


def verify_target_confinement(
    resources: LiveResources, target: str, network: str, ssh_port: int
) -> None:
    capability_text = (
        podman_exec(
            resources,
            target,
            "awk",
            "/^CapEff:/ { print $2 }",
            "/proc/self/status",
            purpose="reading target capabilities",
        )
        .decode()
        .strip()
    )
    try:
        capability_mask = int(capability_text, 16)
    except ValueError as error:
        raise LiveFailure("cannot read the target capability mask") from error

    forbidden = {
        "CAP_DAC_READ_SEARCH": 2,
        "CAP_NET_RAW": 13,
        "CAP_SYS_MODULE": 16,
        "CAP_SYS_RAWIO": 17,
        "CAP_SYS_PTRACE": 19,
        "CAP_SYS_ADMIN": 21,
        "CAP_SYS_BOOT": 22,
        "CAP_SYS_TIME": 25,
        "CAP_MKNOD": 27,
        "CAP_SETFCAP": 31,
    }
    for name, bit in forbidden.items():
        if capability_mask >> bit & 1:
            raise LiveFailure(f"target holds a forbidden capability: {name}")
    for name, bit in {"CAP_SETUID": 7, "CAP_NET_ADMIN": 12}.items():
        if not capability_mask >> bit & 1:
            raise LiveFailure(f"target is missing a required capability: {name}")

    privileged = podman_output(
        resources,
        "inspect",
        "--format",
        "{{.HostConfig.Privileged}}",
        target,
        purpose="inspecting target privilege mode",
    )
    pids_limit = podman_output(
        resources,
        "inspect",
        "--format",
        "{{.HostConfig.PidsLimit}}",
        target,
        purpose="inspecting target process limit",
    )
    if privileged != "false" or pids_limit != "512":
        raise LiveFailure("target confinement does not match the declared policy")
    published = run_process(
        [resources.podman, "port", target, f"{ssh_port}/tcp"],
        text=True,
        capture_output=True,
        check=False,
    )
    if published.stdout.strip():
        raise LiveFailure("the target published its SSH port")
    attached = podman_output(
        resources,
        "inspect",
        "--format",
        "{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}",
        target,
        purpose="inspecting target networks",
    ).split()
    if attached != [network]:
        raise LiveFailure("the target is not isolated to the per-run network")


def provision_target(
    resources: LiveResources,
    image: str,
    policy: list[str],
    public_key: Path,
    public_digest: str,
    network: str,
    ssh_port: int,
) -> str:
    name = unique_name("elsewindow-target")
    resources.target_name = name
    print("live: creating the disposable Xpra target", file=sys.stderr)
    checked(
        [
            resources.podman,
            "create",
            "--name",
            name,
            "--hostname",
            "elsewindow-e2e",
            "--label",
            f"{RUN_LABEL}={name}",
            "--label",
            f"{OWNER_LABEL}=live-target",
            "--network",
            network,
            "--network-alias",
            "live-target",
            *policy,
            image,
        ],
        "creating the target container",
    )
    checked([resources.podman, "start", name], "starting the target container")
    podman_exec(
        resources,
        name,
        "sh",
        "-ceu",
        (
            "umask 077; cat > /home/xpra-test/.ssh/authorized_keys; "
            "chown xpra-test:xpra-test /home/xpra-test/.ssh/authorized_keys; "
            "chmod 0600 /home/xpra-test/.ssh/authorized_keys"
        ),
        purpose="streaming the target public key",
        input_data=public_key.read_bytes(),
    )
    wait_for_sshd(resources, name, ssh_port)
    guest_digest = (
        podman_exec(
            resources,
            name,
            "sha256sum",
            "/home/xpra-test/.ssh/authorized_keys",
            purpose="hashing the installed target key",
        )
        .decode()
        .split()[0]
    )
    if guest_digest != public_digest:
        raise LiveFailure("authorized key differs from the supplied public key")
    groups = (
        podman_exec(
            resources,
            name,
            "id",
            "-Gn",
            "xpra-test",
            purpose="checking the target account",
        )
        .decode()
        .strip()
    )
    if groups != "xpra-test":
        raise LiveFailure("test account has unexpected group access")
    verify_target_confinement(resources, name, network, ssh_port)
    return name
