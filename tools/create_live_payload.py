"""Stream the current project as one bounded tar archive."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import container_payload
from tools.project_tree import ProjectTreeError, project_files


def entries(
    packaged_wheel: Path | None = None,
) -> tuple[container_payload.PayloadEntry, ...]:
    selected = tuple(
        container_payload.PayloadEntry(
            path, PurePosixPath(path.relative_to(ROOT).as_posix())
        )
        for path in project_files(ROOT)
        if packaged_wheel is None or path.relative_to(ROOT).parts[0] != "elsewindow"
    )
    if packaged_wheel is None:
        return selected
    wheel = packaged_wheel.resolve()
    version = (ROOT / ".version").read_text(encoding="utf-8").strip()
    expected = f"elsewindow-{version}-py3-none-any.whl"
    if (
        wheel.parent != (ROOT / "dist").resolve()
        or wheel.name != expected
        or not wheel.is_file()
        or wheel.is_symlink()
    ):
        raise container_payload.PayloadError(
            "packaged live payload requires the exact verified wheel in dist"
        )
    return (
        *selected,
        container_payload.PayloadEntry(wheel, PurePosixPath("release") / wheel.name),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged-wheel", type=Path)
    arguments = parser.parse_args()
    try:
        container_payload.write_archive(
            sys.stdout.buffer, entries(arguments.packaged_wheel)
        )
    except (
        OSError,
        UnicodeError,
        ProjectTreeError,
        container_payload.PayloadError,
    ) as error:
        print(f"live payload: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
