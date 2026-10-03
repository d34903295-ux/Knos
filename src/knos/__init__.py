"""knos - Bounties that pay when the pull request is merged with the checks you named passing. Attested by a GitHub-signed workflow run, verified on Solana."""

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
