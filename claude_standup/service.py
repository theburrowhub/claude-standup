"""OS service management for installing claude-standup as a launchd or systemd service."""

from __future__ import annotations

import os
import platform
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from claude_standup.daemon import is_daemon_running, read_pid_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PID_PATH = str(Path.home() / ".claude-standup" / "daemon.pid")
DEFAULT_LOG_PATH = str(Path.home() / ".claude-standup" / "daemon.log")

# ---------------------------------------------------------------------------
# DaemonStatus
# ---------------------------------------------------------------------------


@dataclass
class DaemonStatus:
    """Snapshot of whether the daemon process is currently alive."""

    running: bool
    pid: int | None

    @classmethod
    def check(cls, pid_path: str = DEFAULT_PID_PATH) -> DaemonStatus:
        """Check the daemon's status via its PID file."""
        pid = read_pid_file(pid_path)
        running = is_daemon_running(pid_path)
        return cls(running=running, pid=pid if running else None)


# ---------------------------------------------------------------------------
# LaunchdManager (macOS)
# ---------------------------------------------------------------------------


class LaunchdManager:
    """Manage a launchd user agent for the claude-standup daemon."""

    LABEL = "com.claude-standup.daemon"

    def __init__(self) -> None:
        self.plist_path = str(
            Path.home() / "Library" / "LaunchAgents" / f"{self.LABEL}.plist"
        )

    def install(self, binary_path: str) -> None:
        """Write the plist file and load it via launchctl."""
        log_path = DEFAULT_LOG_PATH
        plist_content = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
              "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>Label</key>
                <string>{self.LABEL}</string>
                <key>ProgramArguments</key>
                <array>
                    <string>{binary_path}</string>
                    <string>daemon</string>
                    <string>run</string>
                </array>
                <key>RunAtLoad</key>
                <true/>
                <key>KeepAlive</key>
                <true/>
                <key>StandardOutPath</key>
                <string>{log_path}</string>
                <key>StandardErrorPath</key>
                <string>{log_path}</string>
            </dict>
            </plist>
        """)

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.plist_path), exist_ok=True)

        with open(self.plist_path, "w") as f:
            f.write(plist_content)

        subprocess.run(["launchctl", "load", self.plist_path], check=True)

    def uninstall(self) -> None:
        """Unload the agent and remove the plist file."""
        subprocess.run(["launchctl", "unload", self.plist_path], check=True)

        try:
            os.remove(self.plist_path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# SystemdManager (Linux)
# ---------------------------------------------------------------------------


class SystemdManager:
    """Manage a systemd user service for the claude-standup daemon."""

    SERVICE_NAME = "claude-standup"

    def __init__(self) -> None:
        self.service_path = str(
            Path.home()
            / ".config"
            / "systemd"
            / "user"
            / f"{self.SERVICE_NAME}.service"
        )

    def install(self, binary_path: str) -> None:
        """Write the unit file and enable + start the service."""
        unit_content = textwrap.dedent(f"""\
            [Unit]
            Description=Claude Standup Daemon
            After=network.target

            [Service]
            Type=simple
            ExecStart={binary_path} daemon run
            Restart=always
            RestartSec=10

            [Install]
            WantedBy=default.target
        """)

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.service_path), exist_ok=True)

        with open(self.service_path, "w") as f:
            f.write(unit_content)

        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", self.SERVICE_NAME],
            check=True,
        )

    def uninstall(self) -> None:
        """Disable the service, remove the unit file, and reload."""
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", self.SERVICE_NAME],
            check=True,
        )

        try:
            os.remove(self.service_path)
        except FileNotFoundError:
            pass

        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_service_manager() -> LaunchdManager | SystemdManager:
    """Return the appropriate service manager for the current OS."""
    if platform.system() == "Darwin":
        return LaunchdManager()
    return SystemdManager()
