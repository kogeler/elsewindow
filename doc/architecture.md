# Architecture

Session diagnostics use a shared standard-library adapter for journald's native
Unix datagram protocol. It preserves Xpra severity from the public logging
format, bounds records, and keeps traceback continuation severity. Elsewindow
events and Xpra messages have four local/remote identifiers and common
side/session fields. The local journal and terminal receive all four; the
server journal receives only remote events. This does not import Xpra internals
or add a Python dependency.
The ordinary remote relay stays inside the heartbeat-owned process group.
It keeps draining to journald after SSH output closes or stops being consumed,
and never duplicates the owner's group-wide stop signal. Raw stdout and stderr
forwarding uses nonblocking writes with separate bounded queues; excess or
undeliverable bytes are counted and reported without delaying application cleanup.
Normal command exit has a bounded final output drain for a briefly delayed
reader; group termination skips that wait.
Local terminal output and native journal
output share one filter; neither sink owns application lifetime.

`elsewindow.log_transport` supplies a bounded live subscription over channels
of the existing SSH master. Remote journal producers fan out through private
Unix datagram sockets to owned SSH observers, without reading journal history
or granting new permissions. Subscription precedes startup, including the
stable persistent log ID resolved by a read-only identity request. That ID hashes
the remote machine ID, UID, and canonical application key without changing the
application registry key. Early local records have a bounded deferred buffer
and receive this same ID before either sink writes them; failure before identity
negotiation instead flushes with the shared invocation ID. Every journal record
contains the ID both as native metadata and in its message, and every terminal
line retains it. Queues and frames
are bounded; pressure or channel loss does not stop the application or trigger
reconnection. Socket cleanup is limited to each observer's exact owned path.
Persistent protocol compatibility prevents silently resuming an older helper
that cannot publish to this transport.

The project has five runtime layers:

1. `elsewindow.cli` validates public authority, application, environment, profile,
   clipboard, and timeout inputs.
2. `elsewindow.live_config` strictly parses the packaged reviewed YAML and
   exposes its server and client profile blocks.
3. `elsewindow.session` combines dynamic owned paths and the selected
   clipboard and security policy with those blocks, then coordinates local and
   remote Xpra processes.
4. the published
   [`ssh-wrapper`](https://pypi.org/project/ssh-wrapper/) package owns the
   OpenSSH master, mux-only commands, environment recovery, bounded diagnostic
   tails, and heartbeat-supervised remote process group.
5. `elsewindow.persistent` uses that same mux to control the standard-library
   `_persistent_agent` entry point. Only explicit persistent mode replaces the
   heartbeat supervisor with a transient systemd user service.

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

No application code is imported from another project tree. The six root
`requirements*.in` files own the exact direct Python versions and their
generated same-stem locks bind the complete graphs. Published package metadata
reads the runtime input directly, and every tool input includes it so all
maintainer environments bind the exact published `ssh-wrapper` wheel obtained
from PyPI. Separate packaged inputs and locks under `elsewindow/` own the
local Xpra PyOpenGL additions and their temporary build tools; they are not
Elsewindow import dependencies.

`elsewindow.machine` is a standard-library-only namespace selector. Make and the
repository launcher execute it with isolated system Python before selecting
any prepared interpreter. An application-specific keyed hash of the OS machine
ID and local UID separates every repository environment beneath `.venvs/`,
including tooling and the Xpra additions. Installed and standalone commands
reuse that key in their default XDG Xpra environment location. Explicit Xpra
directory overrides remain exact operator-owned locations.

The repository launcher validates its hash-lock marker before execution. It
replaces inherited `PYTHONPATH` with the repository root: `elsewindow` comes
from that reviewed source tree and `ssh_wrapper` comes from the prepared
runtime environment. Launcher startup never installs or repairs either
component.

`elsewindow.xpra_runtime` owns explicit preparation and read-only validation of
the local Xpra environment. Its venv is based on the system Xpra interpreter,
with system site-packages for GTK and native distribution modules. The matched
PyOpenGL pair is installed locally from hash-locked PyPI artifacts. Wheels are
preferred; otherwise the accelerator is compiled from its verified source
archive with separately locked temporary tools and system development headers.
There are no unpinned build-isolation downloads, and only the PyOpenGL pair
remains in the activated venv. An exact owned
launcher invokes the distribution Xpra script through that venv's Python with
isolated imports. Every local session command, including capability checks,
uses this launcher; remote Xpra continues to use its system installation.
Preparation stages a replacement privately and restores the previous owned
environment if activation fails. Startup rechecks both current bundled locks,
system interpreter identity, exact installed versions, and installed file
hashes instead of trusting a previous successful check.

Production session startup, attach, and the live harness's auxiliary Xpra
commands share the package's strict YAML assembler. Auxiliary command builders
add only runtime-selected targets and the owned mux wrapper; their canonical
flags remain package data rather than a second Python table. The session layer
applies the selected clipboard policy and reviewed GUI defaults explicitly to
both Xpra peers after the mirrored `--minimal` base options. `session_bus.py`
owns a private foreground notification bus for each ordinary or persistent
remote session. The journal relay and persistent worker reuse it without
wrapping or changing the recorded Xpra PID or command fingerprint.

The application-side desktop helper starts the distribution portal, GTK backend,
and a transient permission store after Xpra supplies its display environment.
These foreground processes share the owned remote process group or cgroup. Their
absence or startup failure disables portal functionality with package guidance;
the helper still starts the application and preserves its arguments and exit
status. No desktop helper adopts another session's bus or installs packages.
The remote owner also supplies a private one-use application lock. Repeated or
concurrent child-start requests launch neither a second application nor a second
portal owner. This scope remains available without D-Bus and is removed with
the ordinary process group or persistent worker's other owned resources.
Only portal helper processes have bounded CPU affinity and native worker pools;
icon loaders cannot consume one worker per host CPU under a user-service task
limit. The application and Xpra retain their original affinity.

Application environment overrides share the child's bounded JSON launch payload.
The CLI captures explicitly named local values before connecting and normalizes
assignment order. The isolated desktop helper validates the overrides again and
passes them only to the application's `Popen` environment after starting desktop
services. Display, runtime-directory, D-Bus, and Elsewindow control variables are
reserved; the remote helper and Xpra keep their original environment.

The package treats Xpra as a release-backed external application. It validates
only public command surfaces and observes real process, window, picture, and
lifecycle outcomes; it does not import Xpra internals to reproduce codec,
renderer, or downstream-patch tests. Those implementation guarantees belong
to the maintained fork and its live matrices.

## Persistent Session Ownership

The remote agent resolves the executable against the remote login environment,
converts its invocation path to an absolute path, and hashes a JSON argv array.
An explicit application `PATH` overrides the login search path for this lookup.
Executable symlinks are not dereferenced: doing so would change virtualenv or
multicall-binary behavior and keys when versioned application links change.
Argument boundaries and
empty arguments are preserved. The account-local hash names a private lock,
record and transient unit; a fresh random token distinguishes successive
instances with identical argv. Encoding and clipboard policies are not part of
the key, but their complete server command must match before resumption.
The network profile affects only the new local client.
The explicit application environment is recorded separately from the supervisor's
login environment. Resumption requires an identical override mapping, independent
of assignment order, and never modifies an already running application's
environment. The values do not enter public identity replies or mismatch messages.
The control request carries application argv and overrides once, with child
placeholders in the server templates; the remote agent rebuilds the canonical
child command before recording or comparing it.

Under the lock, the agent either verifies an existing service or writes an
atomic record and submits `systemd-run --user`. The service supervisor is
started by the user manager, not the SSH process, with its own control-group
lifetime, no restart and automatic unit collection. Its recorded working
directory and selected login environment are fixed at initial creation.
Xpra starts the foreground child immediately, so an interrupted first attach
does not leave an empty session waiting indefinitely. The same metadata probe,
profile assembler and attach/lifecycle machinery serve both session modes.

Disconnect only cancels observation of the persistent service. Xpra's child
exit policy ends the server for any child exit code; the supervisor removes
its matching record, and systemd reaps remaining cgroup members. Boot does not
restore these transient services. Administrative stops, host failure, and
application delegation to another instance are outside the survival guarantee.

## Distribution Boundaries

PyPI receives one normalized pure-Python wheel and one normalized sdist. Both
contain only the package, metadata, license, README, version source, and build
inputs required by their format. Both package the local Xpra inputs and hash locks.
The sdist carries `requirements.in` because
it is an input to dynamic runtime metadata; internal tool inputs remain outside
the public archive. Clean smoke installs the exact published `ssh-wrapper`
wheel separately and proves that imports do not resolve from the checkout.

GitHub additionally receives native amd64 and arm64 PyInstaller one-file ELF
executables. They bundle the Python runtime, `ssh-wrapper`, `.version`,
`live-cli.yml`, `profiles.yml`, and the standard-library remote agent source.
They do not contain Xpra or system drivers.
Their `--prepare-xpra` route uses the bundled locks and external system Python,
not the frozen Python or extraction directory, to create the same persistent
Xpra venv as installed wheels. Before invoking system tools, the frozen process
restores the host library search path so bundled native libraries cannot leak
into Xpra or its Python bootstrap.
`--diagnose` reports the bundled dependency and resource identities and probes
the external `ssh`, `false`, and `xpra` commands and the prepared Xpra environment.

The live harness uses the same distribution boundary: it removes package
source from the payload, clean-installs the verified wheel in the confined
client, and proves the imported file and distribution metadata before running
the production lifecycle matrix.
