from pathlib import Path
import subprocess

from cnc_light_editor.updater import GitUpdater


CURRENT = "a" * 40
LATEST = "b" * 40


class ScriptedUpdater(GitUpdater):
    def __init__(self, root: Path, *, dirty: bool = False, diverged: bool = False, install_fails: bool = False):
        super().__init__(root, python_executable="python-test")
        self.dirty = dirty
        self.diverged = diverged
        self.install_fails = install_fails
        self.commands: list[tuple[str, ...]] = []

    def _command(self, command: list[str], *, timeout: int, check: bool):
        key = tuple(command)
        self.commands.append(key)
        stdout = ""
        returncode = 0
        if key[:4] == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            stdout = "main\n"
        elif key[:3] == ("git", "status", "--porcelain"):
            stdout = " M local.py\n" if self.dirty else ""
        elif key[:3] == ("git", "rev-parse", "HEAD"):
            stdout = CURRENT + "\n"
        elif key[:3] == ("git", "rev-parse", "origin/main"):
            stdout = LATEST + "\n"
        elif key[:3] == ("git", "merge-base", "--is-ancestor"):
            returncode = 1 if self.diverged else 0
        elif key[:3] == ("python-test", "-m", "pip"):
            returncode = 1 if self.install_fails else 0
        result = subprocess.CompletedProcess(command, returncode, stdout, "failure" if returncode else "")
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command, stdout, result.stderr)
        return result


def updater(tmp_path: Path, **kwargs) -> ScriptedUpdater:
    (tmp_path / ".git").mkdir()
    return ScriptedUpdater(tmp_path, **kwargs)


def test_clean_main_fast_forwards_installs_and_requests_restart(tmp_path):
    subject = updater(tmp_path)
    events = []

    result = subject.run(emit=events.append)

    assert result.state == "restart"
    assert [event.state for event in events] == [
        "checking", "downloading", "installing", "restart",
    ]
    assert ("git", "merge", "--ff-only", "origin/main") in subject.commands
    assert any(command[:3] == ("python-test", "-m", "pip") for command in subject.commands)


def test_tracked_changes_pause_update_before_network_fetch(tmp_path):
    subject = updater(tmp_path, dirty=True)

    result = subject.run()

    assert result.state == "paused"
    assert "tracked local changes" in result.message
    assert not any(command[:2] == ("git", "fetch") for command in subject.commands)


def test_diverged_main_is_never_overwritten(tmp_path):
    subject = updater(tmp_path, diverged=True)

    result = subject.run()

    assert result.state == "paused"
    assert "manual merge" in result.message
    assert not any(command[:2] == ("git", "merge") for command in subject.commands)


def test_failed_install_rolls_source_back_to_previous_commit(tmp_path):
    subject = updater(tmp_path, install_fails=True)

    result = subject.run()

    assert result.state == "failed"
    assert ("git", "reset", "--hard", CURRENT) in subject.commands

