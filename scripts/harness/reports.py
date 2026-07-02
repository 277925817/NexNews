from pathlib import Path


def relative_report_path(path: Path) -> str:
    parts = path.parts
    for marker in ("acceptance", "stages", "tasks", "observability"):
        if marker in parts:
            marker_index = parts.index(marker)
            return Path(*parts[marker_index:]).as_posix()
    return path.as_posix()
