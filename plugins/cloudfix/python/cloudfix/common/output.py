"""JSON output helpers and run-directory handling."""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from cloudfix.common.errors import InputError

_POSIX = os.name == "posix"


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def new_run_dir(path: str | Path) -> Path:
    run_dir = Path(path).resolve()
    if run_dir.exists():
        raise InputError(f"run directory already exists: {run_dir}")
    if not run_dir.parent.is_dir():
        raise InputError(f"parent directory does not exist: {run_dir.parent}")
    run_dir.mkdir(mode=0o700 if _POSIX else 0o777)
    if _POSIX:
        run_dir.chmod(0o700)  # mkdir mode is masked by umask only downward; be explicit
    return run_dir


def write_json(path: Path, obj: Any) -> None:
    """Write through a temporary file in the same directory, then replace `path`, so a failure mid-write
    leaves the previous file intact (execution.json is rewritten after every step)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(to_jsonable(obj), f, indent=2)
            f.write("\n")
        if _POSIX:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(to_jsonable(obj)) + "\n")
    sys.stdout.flush()
