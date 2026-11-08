"""Windows entry point: py -3 lottery_tool.py --help"""
import sys

# The package uses Python 3.10+ syntax; stop with a clear message on older versions.
if sys.version_info < (3, 10):
    sys.stderr.write("Python 3.10 or newer is required; this is Python %d.%d.\n" % sys.version_info[:2])
    raise SystemExit(2)

from bclc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
