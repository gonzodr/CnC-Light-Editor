from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable


@dataclass(frozen=True)
class UpdateEvent:
    state: str
    message: str
    current_sha: str = ""
    latest_sha: str = ""


class GitUpdater:
    """Fast-forward a clean production checkout and reinstall it in-place."""

    def __init__(
        self,
        root: str | Path,
        *,
        python_executable: str | Path = sys.executable,
        remote: str = "origin",
        branch: str = "main",
    ) -> None:
        self.root = Path(root).resolve()
        self.python_executable = str(python_executable)
        self.remote = remote
        self.branch = branch

    def run(
        self,
        *,
        auto_install: bool = True,
        emit: Callable[[UpdateEvent], None] | None = None,
    ) -> UpdateEvent:
        publish = emit or (lambda _event: None)

        def report(state: str, message: str, current: str = "", latest: str = "") -> UpdateEvent:
            event = UpdateEvent(state, message, current, latest)
            publish(event)
            return event

        if not (self.root / ".git").exists():
            return report("unavailable", "Updates require a Git checkout")
        try:
            branch = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if branch != self.branch:
                return report("paused", f"Updates paused on development branch {branch}")
            dirty = self._git("status", "--porcelain", "--untracked-files=no").stdout.strip()
            if dirty:
                return report("paused", "Updates paused: tracked local changes")

            current = self._git("rev-parse", "HEAD").stdout.strip()
            report("checking", "Checking GitHub for updates…", current)
            self._git("fetch", "--quiet", self.remote, self.branch, timeout=90)
            latest_ref = f"{self.remote}/{self.branch}"
            latest = self._git("rev-parse", latest_ref).stdout.strip()
            if current == latest:
                return report("current", "Up to date", current, latest)
            ancestor = self._git(
                "merge-base", "--is-ancestor", current, latest_ref,
                check=False,
            )
            if ancestor.returncode != 0:
                return report(
                    "paused", "Update needs manual merge; local main has diverged",
                    current, latest,
                )
            if not auto_install:
                return report("available", "Update available", current, latest)

            report("downloading", "Downloading update…", current, latest)
            self._git("merge", "--ff-only", latest_ref, timeout=90)
            report("installing", "Installing update…", current, latest)
            install = self._command(
                [
                    self.python_executable, "-m", "pip", "install",
                    "--disable-pip-version-check", "-e", str(self.root),
                ],
                timeout=300,
                check=False,
            )
            if install.returncode != 0:
                self._git("reset", "--hard", current, timeout=60)
                return report(
                    "failed",
                    "Install failed; source rolled back to the previous version",
                    current,
                    latest,
                )
            return report("restart", "Update installed — restarting…", current, latest)
        except (OSError, subprocess.SubprocessError) as error:
            return report("failed", f"Update failed: {error}")

    def _git(
        self, *args: str, timeout: int = 30, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self._command(["git", *args], timeout=timeout, check=check)

    def _command(
        self, command: list[str], *, timeout: int, check: bool,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=check,
        )
