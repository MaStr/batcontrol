"""Shared fixtures for logic tests."""
import pytest

from batcontrol.logic.common import CommonLogic


@pytest.fixture(autouse=True)
def reset_common_logic():
    """Keep the CommonLogic singleton from leaking settings between tests."""
    CommonLogic._instance = None
    yield
    CommonLogic._instance = None
