from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "zerolab-network-config"


@dataclass
class Fixture:
    env: dict[str, str]
    command_log: Path
    notify_log: Path


def _fake_command(path: Path, log: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "printf '%%s\\n' \"$*\" >> \"%s\"\n" % log,
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fake_notify(path: Path, log: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "for arg do printf '%%s\\n' \"$arg\"; done >> \"%s\"\n" % log,
        encoding="utf-8",
    )
    path.chmod(0o755)


def make_fixture(tmp_path: Path, mode: str | None) -> Fixture:
    command_log = tmp_path / "commands.log"
    notify_log = tmp_path / "notify.log"
    ip = tmp_path / "ip"
    sysctl = tmp_path / "sysctl"
    notify = tmp_path / "systemd-notify"
    _fake_command(ip, command_log)
    _fake_command(sysctl, command_log)
    _fake_notify(notify, notify_log)

    env = os.environ.copy()
    if mode is None:
        env.pop("ZEROLAB_NETWORK_MODE", None)
    else:
        env["ZEROLAB_NETWORK_MODE"] = mode
    env.update(
        {
            "ZEROLAB_IP_BIN": str(ip),
            "ZEROLAB_SYSCTL_BIN": str(sysctl),
            "ZEROLAB_SYSTEMD_NOTIFY_BIN": str(notify),
            "ZEROLAB_NETWORK_STATE_DIR": str(tmp_path / "state"),
            "ZEROLAB_RECONCILE_SECONDS": "0.01",
        }
    )
    return Fixture(env=env, command_log=command_log, notify_log=notify_log)


def run_helper(fixture: Fixture, action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HELPER), action],
        cwd=ROOT,
        env=fixture.env,
        text=True,
        capture_output=True,
        check=False,
    )


def start_helper(fixture: Fixture, action: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(HELPER), action],
        cwd=ROOT,
        env=fixture.env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_missing_mode_defaults_to_direct_without_network_commands(tmp_path):
    fixture = make_fixture(tmp_path, mode=None)
    assert run_helper(fixture, "start").returncode == 0
    assert run_helper(fixture, "stop").returncode == 0
    assert not fixture.command_log.exists()


def test_explicit_direct_without_owned_state_does_not_touch_network(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    assert run_helper(fixture, "start").returncode == 0
    assert run_helper(fixture, "stop").returncode == 0
    assert not fixture.command_log.exists()


def test_empty_mode_defaults_to_direct(tmp_path):
    fixture = make_fixture(tmp_path, mode="")
    assert run_helper(fixture, "start").returncode == 0
    assert not fixture.command_log.exists()


def test_invalid_mode_lists_only_supported_values(tmp_path):
    fixture = make_fixture(tmp_path, mode="automatic")
    result = run_helper(fixture, "start")
    assert result.returncode == 2
    assert "direct or alias" in result.stderr


def test_direct_run_notifies_ready_and_stays_active(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    process = start_helper(fixture, "run")
    assert wait_until(fixture.notify_log.exists)
    assert fixture.notify_log.read_text().splitlines() == ["--ready"]
    assert process.poll() is None
    process.terminate()
    assert process.wait(timeout=2) == 0
    assert not fixture.command_log.exists()
