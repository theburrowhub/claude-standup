"""Tests for claude_standup.service module."""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch, MagicMock

import pytest

from claude_standup.service import (
    DaemonStatus,
    LaunchdManager,
    SystemdManager,
    get_service_manager,
)


# ---------------------------------------------------------------------------
# TestGetServiceManager
# ---------------------------------------------------------------------------


class TestGetServiceManager:
    """Tests for get_service_manager() OS detection."""

    def test_macos(self):
        with patch("claude_standup.service.platform.system", return_value="Darwin"):
            mgr = get_service_manager()
            assert isinstance(mgr, LaunchdManager)

    def test_linux(self):
        with patch("claude_standup.service.platform.system", return_value="Linux"):
            mgr = get_service_manager()
            assert isinstance(mgr, SystemdManager)


# ---------------------------------------------------------------------------
# TestLaunchdManager
# ---------------------------------------------------------------------------


class TestLaunchdManager:
    """Tests for the LaunchdManager class."""

    def test_plist_path(self):
        mgr = LaunchdManager()
        assert "LaunchAgents" in mgr.plist_path

    def test_install_writes_plist(self, tmp_path):
        mgr = LaunchdManager()
        plist_file = tmp_path / "com.claude-standup.daemon.plist"
        mgr.plist_path = str(plist_file)

        with patch("claude_standup.service.subprocess.run") as mock_run:
            mgr.install("/usr/local/bin/claude-standup")

        content = plist_file.read_text()
        assert "claude-standup" in content
        assert "daemon" in content
        assert "run" in content
        mock_run.assert_called_once()

    def test_uninstall_removes_plist(self, tmp_path):
        mgr = LaunchdManager()
        plist_file = tmp_path / "com.claude-standup.daemon.plist"
        plist_file.write_text("<plist>dummy</plist>")
        mgr.plist_path = str(plist_file)

        with patch("claude_standup.service.subprocess.run") as mock_run:
            mgr.uninstall()

        assert not plist_file.exists()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# TestSystemdManager
# ---------------------------------------------------------------------------


class TestSystemdManager:
    """Tests for the SystemdManager class."""

    def test_service_path(self):
        mgr = SystemdManager()
        assert "systemd/user" in mgr.service_path

    def test_install_writes_service(self, tmp_path):
        mgr = SystemdManager()
        service_file = tmp_path / "claude-standup.service"
        mgr.service_path = str(service_file)

        with patch("claude_standup.service.subprocess.run") as mock_run:
            mgr.install("/usr/local/bin/claude-standup")

        content = service_file.read_text()
        assert "daemon run" in content
        mock_run.assert_called()


# ---------------------------------------------------------------------------
# TestDaemonStatus
# ---------------------------------------------------------------------------


class TestDaemonStatus:
    """Tests for the DaemonStatus dataclass."""

    def test_status_running(self, tmp_path):
        pid_path = str(tmp_path / "daemon.pid")
        # Write our own PID so the process check succeeds
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))

        status = DaemonStatus.check(pid_path=pid_path)
        assert status.running is True
        assert status.pid == os.getpid()

    def test_status_not_running(self, tmp_path):
        pid_path = str(tmp_path / "nonexistent.pid")
        status = DaemonStatus.check(pid_path=pid_path)
        assert status.running is False
        assert status.pid is None
