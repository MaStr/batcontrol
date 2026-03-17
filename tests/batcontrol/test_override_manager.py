"""Tests for the OverrideManager"""
import time
import pytest

from batcontrol.override_manager import OverrideManager, OverrideState


class TestOverrideState:
    """Test OverrideState dataclass behavior"""

    def test_state_creation(self):
        """Test basic override state creation"""
        state = OverrideState(mode=-1, charge_rate=500, duration_minutes=30, reason="test")
        assert state.mode == -1
        assert state.charge_rate == 500
        assert state.duration_minutes == 30
        assert state.reason == "test"
        assert state.expires_at > state.created_at

    def test_state_remaining(self):
        """Test remaining time calculation"""
        state = OverrideState(mode=0, charge_rate=None, duration_minutes=10, reason="test")
        assert state.remaining_minutes > 9.9
        assert state.remaining_minutes <= 10.0
        assert not state.is_expired

    def test_state_expired(self):
        """Test expired state detection"""
        state = OverrideState(
            mode=0, charge_rate=None, duration_minutes=1, reason="test",
            created_at=time.time() - 120,  # 2 minutes ago
            expires_at=time.time() - 60     # expired 1 minute ago
        )
        assert state.is_expired
        assert state.remaining_seconds == 0.0
        assert state.remaining_minutes == 0.0

    def test_state_to_dict(self):
        """Test serialization to dict"""
        state = OverrideState(mode=-1, charge_rate=500, duration_minutes=30, reason="test charge")
        d = state.to_dict()
        assert d['mode'] == -1
        assert d['charge_rate'] == 500
        assert d['duration_minutes'] == 30
        assert d['reason'] == "test charge"
        assert d['is_active'] is True
        assert 'remaining_minutes' in d
        assert 'created_at' in d
        assert 'expires_at' in d


class TestOverrideManager:
    """Test OverrideManager core functionality"""

    def test_no_override_initially(self):
        """Manager starts with no active override"""
        mgr = OverrideManager()
        assert mgr.get_override() is None
        assert not mgr.is_active()
        assert mgr.remaining_minutes == 0.0

    def test_set_override(self):
        """Test setting an override"""
        mgr = OverrideManager()
        state = mgr.set_override(mode=-1, duration_minutes=30, charge_rate=500, reason="test")
        assert state.mode == -1
        assert state.charge_rate == 500
        assert mgr.is_active()
        assert mgr.remaining_minutes > 29.9

    def test_set_override_default_duration(self):
        """Test that default duration is used when not specified"""
        mgr = OverrideManager(default_duration_minutes=45)
        state = mgr.set_override(mode=0, reason="default duration test")
        assert state.duration_minutes == 45

    def test_clear_override(self):
        """Test clearing an override"""
        mgr = OverrideManager()
        mgr.set_override(mode=-1, duration_minutes=30)
        assert mgr.is_active()

        mgr.clear_override()
        assert not mgr.is_active()
        assert mgr.get_override() is None

    def test_clear_when_no_override(self):
        """Clearing when nothing is set should not raise"""
        mgr = OverrideManager()
        mgr.clear_override()  # should not raise
        assert not mgr.is_active()

    def test_override_expires(self):
        """Test that expired overrides are automatically cleaned up"""
        mgr = OverrideManager()
        mgr.set_override(mode=0, duration_minutes=1, reason="will expire")

        # Manually set the expiry to the past
        mgr._override.expires_at = time.time() - 1

        assert mgr.get_override() is None
        assert not mgr.is_active()

    def test_override_replaces_previous(self):
        """Setting a new override replaces the previous one"""
        mgr = OverrideManager()
        mgr.set_override(mode=-1, duration_minutes=30, reason="first")
        mgr.set_override(mode=10, duration_minutes=60, reason="second")

        override = mgr.get_override()
        assert override.mode == 10
        assert override.reason == "second"
        assert override.duration_minutes == 60

    def test_invalid_duration_raises(self):
        """Zero or negative duration should raise ValueError"""
        mgr = OverrideManager()
        with pytest.raises(ValueError):
            mgr.set_override(mode=0, duration_minutes=0)
        with pytest.raises(ValueError):
            mgr.set_override(mode=0, duration_minutes=-5)

    def test_override_without_charge_rate(self):
        """Test override for non-charging modes"""
        mgr = OverrideManager()
        state = mgr.set_override(mode=10, duration_minutes=60, reason="allow discharge")
        assert state.charge_rate is None
        assert state.mode == 10

    def test_thread_safety(self):
        """Basic test that concurrent set/get doesn't crash"""
        import threading
        mgr = OverrideManager()
        errors = []

        def setter():
            try:
                for _ in range(100):
                    mgr.set_override(mode=-1, duration_minutes=10, reason="thread test")
            except Exception as e:
                errors.append(e)

        def getter():
            try:
                for _ in range(100):
                    mgr.get_override()
                    mgr.is_active()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=setter), threading.Thread(target=getter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
