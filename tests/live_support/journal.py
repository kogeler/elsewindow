# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""Real host-local journals in the existing disposable lifecycle topology."""

from __future__ import annotations

import json
from collections import Counter

from elsewindow.journal import (
    DEFAULT_LOG_LEVEL,
    PRIORITIES,
    source_label,
    terminal_lines,
)
from elsewindow.log_transport import SESSION
from elsewindow.persistent import LINGER_PROMPT

from .process import LiveFailure, LiveResources, checked
from .xpra_target import AGENT_CASE

LIVE_LOG_LEVELS = {
    "linger-declined": DEFAULT_LOG_LEVEL,
    "linger": "debug-clipboard",
    "detach": "info",
    "abrupt": DEFAULT_LOG_LEVEL,
    "persistent": "info",
    # The operator's command keeps the default level.
    AGENT_CASE: DEFAULT_LOG_LEVEL,
}
MAX_JOURNAL_RECORDS = 50_000


def start_client_journal(resources: LiveResources, client: str) -> None:
    # Only the daemon runs as container root, with the same empty capability set.
    # Its socket and journal files live in bounded, private runtime tmpfs.
    checked(
        [
            resources.podman,
            "exec",
            "--user",
            "0",
            "--detach",
            client,
            "/usr/lib/systemd/systemd-journald",
        ],
        "starting the private client journal",
    )
    checked(
        [
            resources.podman,
            "exec",
            client,
            "python3",
            "-c",
            (
                "import socket,time\n"
                "for attempt in range(50):\n"
                " try:\n"
                "  with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as channel:\n"
                "   channel.connect('/run/systemd/journal/socket')\n"
                "  break\n"
                " except OSError:\n"
                "  time.sleep(0.1)\n"
                "else:\n"
                " raise SystemExit('client journal socket is unavailable')\n"
            ),
        ],
        "waiting for the private client journal",
    )


def journal_records(
    resources: LiveResources,
    container: str,
    evidence: dict[str, object],
) -> list[dict[str, str]]:
    prefix = [resources.podman, "exec", "--user", "0", container, "journalctl"]
    checked([*prefix, "--sync"], "synchronizing the disposable journal")
    result = checked(
        [
            *prefix,
            "--no-pager",
            "--all",
            "--output=json",
            f"--lines={MAX_JOURNAL_RECORDS}",
            *(
                f"--identifier={source_label(side, xpra)}"
                for side in ("client", "server")
                for xpra in (False, True)
            ),
            (
                "--output-fields=MESSAGE,PRIORITY,SYSLOG_IDENTIFIER,ELSEWINDOW_SIDE,"
                "ELSEWINDOW_SESSION,ELSEWINDOW_LOG_LEVEL,ELSEWINDOW_CATEGORY,"
                "ELSEWINDOW_COMPONENT,ELSEWINDOW_FORWARDED,ELSEWINDOW_SOURCE_PID"
            ),
            f"ELSEWINDOW_LOG_LEVEL={evidence['log_level']}",
            *(
                f"ELSEWINDOW_SESSION={session}"
                for session in sorted(
                    set(evidence["sessions"])
                    | set(evidence.get("provisional_sessions", []))
                )
            ),
        ],
        "reading the disposable journal",
    )
    lines = result.stdout.splitlines()
    if len(lines) >= MAX_JOURNAL_RECORDS:
        raise LiveFailure(
            "live journal record limit reached; refusing a truncated comparison"
        )
    return [json.loads(line) for line in lines]


def select_records(
    records: list[dict[str, str]], side: str, evidence: dict[str, object]
) -> list[dict[str, str]]:
    if any(
        record.get("ELSEWINDOW_SESSION") in evidence.get("provisional_sessions", [])
        for record in records
    ):
        raise LiveFailure(
            "startup logs used a provisional ID after successful identification"
        )
    selected = [
        record
        for record in records
        if record.get("ELSEWINDOW_SESSION") in evidence["sessions"]
        and record.get("ELSEWINDOW_LOG_LEVEL") == evidence["log_level"]
    ]
    for record in selected:
        session = record.get("ELSEWINDOW_SESSION", "")
        if SESSION.fullmatch(session) is None or not record.get(
            "MESSAGE", ""
        ).startswith(f"[session={session}] "):
            raise LiveFailure("journal message is missing its shared session identity")
        level = record["ELSEWINDOW_LOG_LEVEL"]
        priority = int(record["PRIORITY"])
        clipboard = (
            level == "debug-clipboard"
            and "clipboard" in record.get("ELSEWINDOW_CATEGORY", "").lower()
        )
        origin = record.get("ELSEWINDOW_SIDE")
        component = record.get("ELSEWINDOW_COMPONENT")
        if (
            origin not in ({"client", "server"} if side == "client" else {"server"})
            or component not in {"xpra", "elsewindow"}
            or record.get("SYSLOG_IDENTIFIER")
            != source_label(origin, component == "xpra")
            or record.get("ELSEWINDOW_FORWARDED")
            != str(int(side == "client" and origin == "server"))
        ) or (
            priority > PRIORITIES.get(level, PRIORITIES["warning"]) and not clipboard
        ):
            raise LiveFailure(
                "journal side or severity differs from the selected policy"
            )
    return selected


def verify_journals(
    resources: LiveResources,
    target: str,
    client: str,
    evidence: dict[str, object],
    stdout: str,
    stderr: str,
    *,
    terminal_merged: bool,
) -> None:
    local = select_records(
        journal_records(resources, client, evidence), "client", evidence
    )
    remote = select_records(
        journal_records(resources, target, evidence), "server", evidence
    )
    if evidence["log_level"] == "info":
        for records, origins in ((local, ("client", "server")), (remote, ("server",))):
            if {record["SYSLOG_IDENTIFIER"] for record in records} != {
                source_label(origin, xpra)
                for origin in origins
                for xpra in (False, True)
            }:
                raise LiveFailure(
                    "a host journal is missing Xpra or Elsewindow lifecycle records"
                )
        for session in set(evidence["sessions"]):
            if {
                record["ELSEWINDOW_SIDE"]
                for record in local
                if record["ELSEWINDOW_SESSION"] == session
            } != {"client", "server"}:
                raise LiveFailure(
                    "the two peers do not share one journal session identity"
                )

    def record_key(record: dict[str, str]) -> tuple[str, ...]:
        return tuple(
            record.get(field, "")
            for field in (
                "ELSEWINDOW_SESSION",
                "SYSLOG_IDENTIFIER",
                "PRIORITY",
                "MESSAGE",
                "ELSEWINDOW_CATEGORY",
                "ELSEWINDOW_SOURCE_PID",
            )
        )

    forwarded = Counter(
        record_key(record) for record in local if record["ELSEWINDOW_SIDE"] == "server"
    )
    originals = Counter(record_key(record) for record in remote)
    if forwarded - originals:
        raise LiveFailure("a forwarded log lacks an identical server journal record")
    if evidence["log_level"] == "debug-clipboard":
        for records in (local, remote):
            if not any(
                int(record["PRIORITY"]) == PRIORITIES["debug"] for record in records
            ):
                raise LiveFailure("clipboard debug was not enabled on both hosts")
    expected: list[Counter[str]] = [Counter(), Counter()]
    for record in local:
        stream = (
            0
            if terminal_merged or int(record["PRIORITY"]) > PRIORITIES["warning"]
            else 1
        )
        expected[stream].update(
            terminal_lines(record["SYSLOG_IDENTIFIER"], record["MESSAGE"].encode())
        )
    for output, messages in zip((stdout, stderr), expected, strict=True):
        if terminal_merged:
            # Pre-supplied PTY input may echo before the prompt is written.
            # The consent prompt is terminal UI, not a severity-filtered log.
            output = output.replace(LINGER_PROMPT, "")
        actual = Counter(
            line
            for line in output.splitlines()
            if line.startswith(
                tuple(
                    f"{source_label(side, xpra)}: "
                    for side in ("client", "server")
                    for xpra in (False, True)
                )
            )
        )
        if actual != messages:
            raise LiveFailure(
                "local terminal and journal messages or severity streams differ "
                f"(terminal={sum(actual.values())}, journal={sum(messages.values())}, "
                f"terminal-only={sum((actual - messages).values())}, "
                f"journal-only={sum((messages - actual).values())})"
            )
