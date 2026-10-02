"""`python -m knos ...`: the same commands as the `knos` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
