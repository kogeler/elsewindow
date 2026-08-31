# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

"""PyInstaller entry point for the standalone Elsewindow executable."""

from __future__ import annotations

from elsewindow.cli import main

raise SystemExit(main())
