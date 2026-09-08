#!/usr/bin/env bash

# Install the SSH target used by the disposable Xpra live test.

set -euo pipefail

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes \
    --no-install-recommends \
    dbus-user-session \
    iproute2 \
    libpam-systemd \
    openssh-server \
    procps \
    rsync \
    sudo \
    systemd-sysv
rm -rf /var/lib/apt/lists/*

# Ubuntu assigns the OpenSSH privilege-separation account to nogroup (65534).
# The disposable target needs no such legacy high GID; keeping it would consume
# an entire rootless subordinate-ID allocation before the client can start.
# Bind the account to OpenSSH's dedicated low-ID group and verify the result.
getent group _ssh >/dev/null
usermod --gid _ssh sshd
test "$(id -g sshd)" = "$(getent group _ssh | cut -d: -f3)"

# Rootless execution cannot run PAM's setgid unix_chkpwd helper on every
# supported host kernel. The target authenticates only by its ephemeral SSH
# key. Sudo keeps common-auth for password-required refusal; SSH, sudo and the
# user manager use local unconditional account checks in this disposable image.
useradd --create-home --uid 1001 --user-group --shell /bin/bash xpra-test
passwd --delete xpra-test
install -d -m 0700 -o xpra-test -g xpra-test /home/xpra-test/.ssh
# SSH remains public-key-only; PAM registers the real logind session.
install -m 0644 /usr/lib/pam.d/systemd-user /etc/pam.d/systemd-user
for service in sudo sshd systemd-user; do
    sed -i \
        's/^@include common-account$/account required pam_permit.so/' \
        "/etc/pam.d/$service"
    grep -Fx 'account required pam_permit.so' "/etc/pam.d/$service"
done
systemctl disable ssh.service ssh.socket
