"""Tests for the startup eyecatcher banner."""

import logging

from batcontrol import banner
from batcontrol.__pkginfo__ import __version__


def test_banner_contains_artwork_and_catch_phrase():
    text = banner.get_startup_banner('1.2.3')
    for art_line in banner.ASCII_ART:
        assert art_line in text
    assert banner.CATCH_PHRASE in text
    assert 'v1.2.3' in text


def test_banner_defaults_to_package_version():
    assert f"v{__version__}" in banner.get_startup_banner()


def test_banner_is_surrounded_by_empty_lines():
    lines = banner.get_startup_banner().split('\n')
    assert lines[0] == ''
    assert lines[1] == ''
    assert lines[-1] == ''
    assert lines[-2] == ''


def test_banner_is_ascii_only_and_has_no_trailing_whitespace():
    for line in banner.get_startup_banner().split('\n'):
        assert line.isascii()
        assert line == line.rstrip()


def test_banner_artwork_fits_banner_width():
    assert max(len(line) for line in banner.ASCII_ART) == banner.BANNER_WIDTH


def test_log_startup_banner_emits_single_info_record(caplog):
    with caplog.at_level(logging.INFO, logger='batcontrol.banner'):
        banner.log_startup_banner('9.9.9')

    records = [r for r in caplog.records if r.name == 'batcontrol.banner']
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert banner.CATCH_PHRASE in records[0].getMessage()
    assert 'v9.9.9' in records[0].getMessage()
