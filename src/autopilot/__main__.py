"""Allow `python -m autopilot` to behave exactly like the installed console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
