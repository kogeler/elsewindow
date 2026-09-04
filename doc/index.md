# Elsewindow

Elsewindow starts one graphical application on a remote Linux host and shows
its windows locally through Xpra. One owned OpenSSH ControlMaster carries every
control channel. Elsewindow opens no forwarding or Xpra TCP listener and
removes only the process group, sockets, and runtime state owned by that
invocation.

The application renders through the remote Wayland compositor and remote GPU;
only Xpra picture, input, clipboard, and control traffic crosses SSH.

## Start here

- [Install and run Elsewindow](getting-started.md) on Linux with CPython 3.13
  or 3.14.
- Choose and review [Xpra behavior and network profiles](xpra.md).
- Understand the [security and cleanup boundary](security.md).
- Read the [architecture](architecture.md) before changing lifecycle code.

Maintainers can continue with the [development workflow](development.md),
[contribution guide](contributing.md), and the permanent contracts under
[maintenance](maintenance/DEVELOPMENT.md).

## Runtime boundary

Elsewindow depends on the exact reviewed `ssh-wrapper` release and on native
OpenSSH and the [maintained Xpra fork](xpra.md#why-the-maintained-xpra-fork-is-required).
Standalone Linux executables bundle Elsewindow, CPython, `ssh-wrapper`, and the
reviewed YAML profiles; they do not bundle Xpra, OpenSSH, Podman, GPU drivers,
or distribution packages.

The project is MIT licensed. Source, issues, and release history are available
in the [public repository](https://github.com/kogeler/elsewindow).
