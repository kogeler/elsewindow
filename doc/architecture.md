# Architecture

The project has four runtime layers:

1. `elsewindow.cli` validates public authority, application, profile,
   clipboard, and timeout inputs.
2. `elsewindow.live_config` strictly parses the packaged reviewed YAML and
   exposes its server and client profile blocks.
3. `elsewindow.session` combines dynamic owned paths and the selected
   clipboard and security policy with those blocks, then coordinates local and
   remote Xpra processes.
4. the published
   [`ssh-wrapper`](https://pypi.org/project/ssh-wrapper/0.1.0/) package owns the
   OpenSSH master, mux-only commands, environment recovery, bounded diagnostic
   tails, and heartbeat-supervised remote process group.

The server always uses Wayland. Its compositor allocates `wayland-N`
atomically and publishes the selected display through `displayfd`. The client
attaches through the existing OpenSSH mux rather than a forwarding socket.
Applications render on the remote machine; the data path carries captured
pixels, input, clipboard synchronization, and Xpra control traffic.

`tools/install_xpra_release.py` is intentionally independent of the Python
package and uses only the standard library. `tools/prepare_xpra_images.py`
feeds a validated release into the Ubuntu target and Debian client build
contexts, verifies labels and installed capabilities, and publishes immutable
image IDs to a private atomic descriptor.

No application code is imported from another project tree. Six
`requirements*.in` files own the exact direct Python versions and their
generated same-stem locks bind the complete graphs. Published package metadata
reads the runtime input directly, and every tool input includes it so all
environments bind the exact published `ssh-wrapper` wheel obtained from PyPI.

The repository launcher validates its hash-lock marker before execution. It
replaces inherited `PYTHONPATH` with the repository root: `elsewindow` comes
from that reviewed source tree and `ssh_wrapper` comes from the prepared
runtime environment. Launcher startup never installs or repairs either
component.

Production session startup, attach, and the live harness's auxiliary Xpra
commands share the package's strict YAML assembler. Auxiliary command builders
add only runtime-selected targets and the owned mux wrapper; their canonical
flags remain package data rather than a second Python table. The session layer
applies the selected clipboard policy explicitly to both Xpra peers after the
mirrored `--minimal` base options.

The package treats Xpra as a release-backed external application. It validates
only public command surfaces and observes real process, window, picture, and
lifecycle outcomes; it does not import Xpra internals to reproduce codec,
renderer, or downstream-patch tests. Those implementation guarantees belong
to the maintained fork and its live matrices.

## Distribution Boundaries

PyPI receives one normalized pure-Python wheel and one normalized sdist. Both
contain only the package, metadata, license, README, version source, and build
inputs required by their format. The sdist carries `requirements.in` because
it is an input to dynamic runtime metadata; internal tool inputs remain outside
the public archive. Clean smoke installs the exact published `ssh-wrapper`
wheel separately and proves that imports do not resolve from the checkout.

GitHub additionally receives native amd64 and arm64 PyInstaller one-file ELF
executables. They bundle the Python runtime, `ssh-wrapper`, `.version`,
`live-cli.yml`, and `profiles.yml`. They do not contain Xpra or system drivers.
`--diagnose` reports the bundled dependency and resource identities and probes
the external `ssh`, `false`, and `xpra` commands.

The live harness uses the same distribution boundary: it removes package
source from the payload, clean-installs the verified wheel in the confined
client, and proves the imported file and distribution metadata before running
the production lifecycle matrix.
