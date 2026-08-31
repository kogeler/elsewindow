# Compatibility And Security Contract

Linux on CPython 3.13 and 3.14 is the complete Python compatibility claim.
The wheel is `py3-none-any`, but its metadata deliberately rejects earlier and
later interpreters and declares no Windows or macOS support. Standalone
artifacts are native 64-bit Linux ELF files named only
`elsewindow-linux-amd64` and `elsewindow-linux-arm64`.

CI runs the complete suite on both Python minors. The compatibility container
also resolves and installs the Python 3.13 graph from hashes. Native standalone
jobs resolve the reviewed PyInstaller graph on each supported architecture.

Governance and live containers run rootless with bounded automatic user
namespaces, private namespaces, reduced capabilities, read-only filesystems
where applicable, bounded resources, and no host filesystem channel. The
governance and live ranges use 2,048 IDs. The target image assigns OpenSSH's
privilege-separation account to its dedicated low-ID `_ssh` group, so the
range covers the highest consumed application UID/GID without allocating the
host's complete subordinate-ID pool. Installer guests use 4,096 IDs.

`make test-network-block` proves the offline governance boundary, while
`make confinement-test` verifies effective UID, capabilities, NoNewPrivs,
seccomp, mount isolation, and absence of credential sockets. All container
payloads pass through [`tools/container_payload.py`](../../tools/container_payload.py).
