#!/usr/bin/env python3
"""Build and execute the ul_mqtt retry behaviour test."""

import subprocess
from pathlib import Path
import sys


def main() -> int:
    test_dir = Path(__file__).resolve().parent
    project_root = test_dir.parents[3]
    build_dir = project_root / "build-tests"
    build_dir.mkdir(parents=True, exist_ok=True)

    source = test_dir / "test_ul_mqtt_retry.c"
    executable = build_dir / "test_ul_mqtt_retry"

    compile_cmd = [
        "gcc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(test_dir),
        str(source),
        "-o",
        str(executable),
    ]

    subprocess.run(compile_cmd, check=True)
    subprocess.run([str(executable)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
