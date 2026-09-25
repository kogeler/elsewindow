"""SSH connection material and confined Xpra-client provisioning."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path, PurePosixPath

from tools import container_payload
from tools.runtime_dependency import runtime_version

from .process import (
    CLIENT_HOME,
    CLIENT_XPRA_VENV,
    OWNER_LABEL,
    RUN_LABEL,
    TARGET_ALIAS,
    ConnectionFiles,
    KeyMaterial,
    LiveFailure,
    LiveResources,
    checked,
    key_fingerprint,
    podman_exec,
    podman_output,
)
from .topology import unique_name


def write_ssh_config(path: Path, *, identity: Path, known_hosts: Path) -> None:
    path.write_text(
        "\n".join(
            (
                f"Host {TARGET_ALIAS}",
                "    HostName live-target",
                "    Port 22",
                "    User xpra-test",
                f'    IdentityFile "{identity}"',
                "    IdentitiesOnly yes",
                "    IdentityAgent none",
                "    PreferredAuthentications publickey",
                "    PasswordAuthentication no",
                "    KbdInteractiveAuthentication no",
                "    StrictHostKeyChecking yes",
                f'    UserKnownHostsFile "{known_hosts}"',
                "    CheckHostIP no",
                "    UpdateHostKeys no",
                "    ControlMaster no",
                "    ControlPersist no",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def prepare_connection_files(
    resources: LiveResources,
    target: str,
    test_dir: Path,
) -> ConnectionFiles:
    host_key = test_dir / "host-key.pub"
    host_key.write_bytes(
        podman_exec(
            resources,
            target,
            "cat",
            "/etc/ssh/ssh_host_ed25519_key.pub",
            purpose="reading the target host key",
        )
    )
    host_key.chmod(0o600)
    expected = key_fingerprint(host_key)
    known_hosts = test_dir / "known_hosts"
    known_hosts.write_text(
        f"live-target {host_key.read_text(encoding='utf-8').strip()}\n",
        encoding="utf-8",
    )
    known_hosts.chmod(0o600)
    observed = key_fingerprint(known_hosts)
    if observed != expected:
        raise LiveFailure("generated known-hosts entry does not match the target")

    ssh_config = test_dir / "ssh_config"
    write_ssh_config(
        ssh_config,
        identity=CLIENT_HOME / ".ssh/id_ed25519",
        known_hosts=CLIENT_HOME / ".ssh/known_hosts",
    )
    return ConnectionFiles(
        expected_host_key=expected,
        observed_host_key=observed,
        ssh_config=ssh_config,
        known_hosts=known_hosts,
    )


def receive_work_tree(test_dir: Path, staging: Path) -> tuple[Path, str]:
    incoming = test_dir / "incoming-work-tree"
    archive = test_dir / "client-source.tar"
    container_payload.extract_archive(sys.stdin.buffer, incoming)
    paths = tuple(
        path.relative_to(incoming).as_posix()
        for path in incoming.rglob("*")
        if path.is_file()
    )
    required = {
        "tests/live_xpra_e2e.py",
    }
    wheel_names = tuple(
        name
        for name in paths
        if name.startswith("release/elsewindow-") and name.endswith("-py3-none-any.whl")
    )
    if (
        not required.issubset(paths)
        or len(wheel_names) != 1
        or any(name.startswith("elsewindow/") for name in paths)
    ):
        raise LiveFailure("live payload is not bound to one packaged Elsewindow wheel")
    if any(
        name == ".live-client" or name.startswith(".live-client/") for name in paths
    ):
        raise LiveFailure("work tree contains the reserved .live-client path")
    entries = [
        container_payload.PayloadEntry(path, PurePosixPath(relative))
        for relative in paths
        if (path := incoming / relative).is_file()
    ]
    entries.extend(
        container_payload.PayloadEntry(
            path,
            PurePosixPath(".live-client") / path.relative_to(staging).as_posix(),
        )
        for path in staging.rglob("*")
        if path.is_file()
    )
    with archive.open("xb") as destination:
        container_payload.write_archive(destination, entries)
    archive.chmod(0o600)
    shutil.rmtree(incoming)
    return archive, wheel_names[0]


def verify_client_confinement(
    resources: LiveResources, client: str, network: str
) -> None:
    status = podman_exec(
        resources,
        client,
        "cat",
        "/proc/self/status",
        purpose="reading client confinement status",
    ).decode()
    uid = (
        podman_exec(resources, client, "id", "-u", purpose="reading client uid")
        .decode()
        .strip()
    )
    if uid == "0" or "CapEff:\t0000000000000000" not in status:
        raise LiveFailure("client is root or holds effective capabilities")
    if "NoNewPrivs:\t1" not in status or "Seccomp:\t2" not in status:
        raise LiveFailure("client privilege or seccomp policy is not active")
    read_only = podman_output(
        resources,
        "inspect",
        "--format",
        "{{.HostConfig.ReadonlyRootfs}}",
        client,
        purpose="inspecting the client root filesystem",
    )
    pids_limit = podman_output(
        resources,
        "inspect",
        "--format",
        "{{.HostConfig.PidsLimit}}",
        client,
        purpose="inspecting the client process limit",
    )
    mounts_text = podman_output(
        resources,
        "inspect",
        "--format",
        "{{json .Mounts}}",
        client,
        purpose="inspecting client mounts",
    )
    mounts = json.loads(mounts_text)
    if not isinstance(mounts, list):
        raise LiveFailure("Podman returned malformed client mount metadata")
    allowed_tmpfs = {"/tmp", "/work", "/home/box", "/run"}
    for mount in mounts:
        if not isinstance(mount, dict):
            raise LiveFailure("Podman returned malformed client mount metadata")
        if (
            mount.get("Type") != "tmpfs"
            or mount.get("Destination") not in allowed_tmpfs
        ):
            raise LiveFailure("client has a bind, volume, or unexpected mount")
    attached = podman_output(
        resources,
        "inspect",
        "--format",
        "{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}",
        client,
        purpose="inspecting client networks",
    ).split()
    if read_only != "true" or pids_limit != "1024":
        raise LiveFailure("client filesystem or resource policy was not applied")
    if attached != [network]:
        raise LiveFailure("client is not isolated to the per-run network")


def provision_client(
    resources: LiveResources,
    image: str,
    policy: list[str],
    network: str,
    connection: ConnectionFiles,
    key: KeyMaterial,
    test_dir: Path,
) -> str:
    client = unique_name("elsewindow-client")
    resources.client_name = client
    print("live: creating the confined Xpra client", file=sys.stderr)
    checked(
        [
            resources.podman,
            "create",
            "--name",
            client,
            "--label",
            f"{RUN_LABEL}={client}",
            "--label",
            f"{OWNER_LABEL}=live-client",
            "--network",
            network,
            "--env",
            f"ELSEWINDOW_XPRA_VENV={CLIENT_XPRA_VENV}",
            "--entrypoint",
            '["/usr/bin/tini","--","sleep","infinity"]',
            *policy,
            image,
        ],
        "creating the client container",
    )
    checked([resources.podman, "start", client], "starting the client container")

    staging = test_dir / "client-stage/home/.ssh"
    staging.mkdir(parents=True, mode=0o700)
    shutil.copy2(connection.ssh_config, staging / "config")
    shutil.copy2(connection.known_hosts, staging / "known_hosts")
    shutil.copy2(key.identity_file, staging / "id_ed25519")
    for path in staging.iterdir():
        path.chmod(0o600)
    archive, wheel_name = receive_work_tree(test_dir, staging.parents[1])
    prepare_script = (
        'install -d -m 0700 "$HOME/.ssh"; '
        'cp -- .live-client/home/.ssh/config "$HOME/.ssh/config"; '
        'cp -- .live-client/home/.ssh/known_hosts "$HOME/.ssh/known_hosts"; '
        'cp -- .live-client/home/.ssh/id_ed25519 "$HOME/.ssh/id_ed25519"; '
        'chmod 0600 "$HOME/.ssh/config" "$HOME/.ssh/known_hosts" '
        '"$HOME/.ssh/id_ed25519"; find .live-client -depth -delete'
    )
    with archive.open("rb") as source:
        checked(
            [
                resources.podman,
                "exec",
                "--interactive",
                client,
                "/usr/local/sbin/toolbox-entrypoint",
                "sh",
                "-ceu",
                prepare_script,
            ],
            "streaming the work tree into the client",
            stdin=source,
        )
    podman_exec(
        resources,
        client,
        "/usr/local/bin/python",
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-index",
        "--no-deps",
        "--user",
        "/work/src/" + wheel_name,
        purpose="clean-installing the verified Elsewindow wheel",
    )
    podman_exec(
        resources,
        client,
        "/usr/local/bin/python",
        "-c",
        (
            "import importlib.metadata, importlib.resources, pathlib, elsewindow, ssh_wrapper; "
            "assert pathlib.Path(elsewindow.__file__).is_relative_to(pathlib.Path('/home/box/.local')); "
            "assert importlib.metadata.version('elsewindow') == elsewindow.__version__; "
            f"assert importlib.metadata.version('ssh-wrapper') == {runtime_version()!r}; "
            "assert importlib.resources.files('elsewindow').joinpath('live-cli.yml').read_bytes(); "
            "assert importlib.resources.files('elsewindow').joinpath('profiles.yml').read_bytes()"
        ),
        purpose="verifying the clean-installed product and dependency",
    )
    verify_client_confinement(resources, client, network)
    podman_exec(
        resources,
        client,
        "env",
        "PIP_NO_INDEX=1",
        "PIP_FIND_LINKS=/usr/local/share/elsewindow/xpra-wheels",
        "/usr/local/bin/python",
        "-m",
        "elsewindow",
        "--prepare-xpra",
        purpose="preparing the packaged local Xpra environment from verified offline wheels",
    )
    return client


def verify_ssh_settings(
    resources: LiveResources, connection: ConnectionFiles, client: str
) -> None:
    settings = podman_exec(
        resources,
        client,
        "ssh",
        "-G",
        TARGET_ALIAS,
        purpose="resolving client SSH configuration",
    ).decode()
    expected = (
        "hostname live-target",
        "port 22",
        "user xpra-test",
        "stricthostkeychecking true",
        "identityagent none",
        "passwordauthentication no",
        "kbdinteractiveauthentication no",
    )
    setting_lines = set(settings.splitlines())
    for value in expected:
        if value not in setting_lines:
            raise LiveFailure(f"SSH setting is not effective: {value}")
    if connection.expected_host_key != connection.observed_host_key:
        raise LiveFailure("target host-key evidence changed before the run")


def target_log(resources: LiveResources, target: str) -> str:
    completed = checked(
        [
            resources.podman,
            "exec",
            target,
            "journalctl",
            "--unit=elsewindow-sshd.service",
            "--output=cat",
            "--no-pager",
        ],
        "reading the target log",
    )
    return (completed.stdout + completed.stderr).decode(errors="replace")
