"""knos - AI agent work counts only when Knos proves it: your coding agent cannot say done, and a hired agent cannot get paid, until the proof is real."""

from importlib.metadata import PackageNotFoundError, version as _installed


def version() -> str:
    """The installed version, so nothing is ever told the empty string.

    Read from package metadata rather than repeated here, because a second
    copy of the number is a second thing to forget to bump. It lives in this
    module so `knos --version` and the MCP handshake give the same answer
    without the terminal having to import the MCP SDK to find out.
    """
    try:
        return _installed("knos")
    except PackageNotFoundError:  # a source tree, not an install
        return "0+unknown"
