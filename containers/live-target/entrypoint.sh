#!/usr/bin/env bash

# Start the OpenSSH server of the disposable Xpra live-test target.
#
# Host keys are generated per container rather than baked into an image layer,
# so no key material is ever stored, shared between runs, or pushed anywhere.
# The target journal records every authentication. A real systemd/logind and
# user manager exercise the production persistent-session boundary.

set -euo pipefail

install -d -m 0755 /run/sshd
ssh-keygen -A >/dev/null

exec /sbin/init
