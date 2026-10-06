""" Startup eyecatcher for batcontrol.

Renders an ASCII art banner plus the catch phrase and the package version.
It is emitted once, as a single multi-line INFO record, so that the log
formatter only prefixes the first (empty) line and the artwork itself stays
unindented and readable.
"""

import logging

from .__pkginfo__ import __version__

logger = logging.getLogger(__name__)

# Keep this ASCII-only (see CLAUDE.md). Width of the artwork below.
BANNER_WIDTH = 72

CATCH_PHRASE = "riding your power prices"

# 'BATCONTROL' in a block letter font, 72 columns wide.
ASCII_ART = [
    r" ____      _     _____   ____   ___   _   _  _____  ____    ___   _",
    r"| __ )    / \   |_   _| / ___| / _ \ | \ | ||_   _||  _ \  / _ \ | |",
    r"|  _ \   / _ \    | |  | |    | | | ||  \| |  | |  | |_) || | | || |",
    r"| |_) | / ___ \   | |  | |___ | |_| || |\  |  | |  |  _ < | |_| || |___",
    r"|____/ /_/   \_\  |_|   \____| \___/ |_| \_|  |_|  |_| \_\ \___/ |_____|",
]


def get_startup_banner(version: str = __version__) -> str:
    """Build the startup banner.

    Args:
        version (str): Version string to display below the artwork.

    Returns:
        str: The banner, surrounded by empty lines above and below.
    """
    lines = ['', '']
    lines += ASCII_ART
    lines += [
        '',
        CATCH_PHRASE.center(BANNER_WIDTH).rstrip(),
        f"v{version}".center(BANNER_WIDTH).rstrip(),
        '',
        '',
    ]
    return '\n'.join(lines)


def log_startup_banner(version: str = __version__) -> None:
    """Log the startup banner once as a single INFO record."""
    logger.info('%s', get_startup_banner(version))
