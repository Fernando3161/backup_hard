"""Stand-in `adb` for tests: runs device commands in a local POSIX shell.

Supports the subset of adb that backup_hard calls:
  -s SERIAL shell CMD          -> sh -c CMD   (stdin forwarded)
  -s SERIAL pull SRC DST       -> copy file
  -s SERIAL exec-out cat -- P  -> write file bytes to stdout
"""
import shutil
import subprocess
import sys

args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
if args[0] == "shell":
    sys.exit(subprocess.run([shutil.which("sh"), "-c", args[1]]).returncode)
if args[0] == "pull":
    try:
        shutil.copyfile(args[1], args[2])
    except OSError as exc:
        print(f"adb: error: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if args[:2] == ["exec-out", "cat"]:
    with open(args[-1], "rb") as handle:
        shutil.copyfileobj(handle, sys.stdout.buffer)
    sys.exit(0)
print(f"unsupported: {args}", file=sys.stderr)
sys.exit(2)
