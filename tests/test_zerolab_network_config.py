from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "zerolab-network-config"


@dataclass
class Fixture:
    env: dict[str, str]
    command_log: Path
    notify_log: Path
    address_state: Path
    arp_state: Path


def _fake_ip(path: Path, log: Path, address_state: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'ip %%s\\n' \"$*\" >> %s\n"
        "state=%s\n"
        "case \"$*\" in\n"
        "  '-4 address show dev '*)\n"
        "    if [ \"${ZEROLAB_FAKE_IP_FAIL_ACTION:-}\" = show ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    interface=$5\n"
        "    while IFS=' ' read -r address entry_interface; do\n"
        "      if [ \"$entry_interface\" = \"$interface\" ]; then\n"
        "        printf '    inet %%s scope global\\n' \"$address\"\n"
        "      fi\n"
        "    done < \"$state\"\n"
        "    ;;\n"
        "  'address add '*)\n"
        "    if [ \"${ZEROLAB_FAKE_IP_FAIL_ACTION:-}\" = add ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    address=$3\n"
        "    interface=$5\n"
        "    if grep -Fqx \"$address $interface\" \"$state\"; then\n"
        "      exit 2\n"
        "    fi\n"
        "    printf '%%s %%s\\n' \"$address\" \"$interface\" >> \"$state\"\n"
        "    ;;\n"
        "  'address del '*)\n"
        "    if [ \"${ZEROLAB_FAKE_IP_FAIL_ACTION:-}\" = del ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    address=$3\n"
        "    interface=$5\n"
        "    temporary=$state.new\n"
        "    grep -Fvx \"$address $interface\" \"$state\" > \"$temporary\" || true\n"
        "    mv \"$temporary\" \"$state\"\n"
        "    ;;\n"
        "  *)\n"
        "    exit 2\n"
        "    ;;\n"
        "esac\n" % (shlex.quote(str(log)), shlex.quote(str(address_state))),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fake_sysctl(path: Path, log: Path, arp_state: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'sysctl %%s\\n' \"$*\" >> %s\n"
        "state=%s\n"
        "case \"$1\" in\n"
        "  -n)\n"
        "    cat \"$state\"\n"
        "    ;;\n"
        "  -w)\n"
        "    value=${2#*=}\n"
        "    case \"$value\" in\n"
        "      ''|*[!0-9]*) exit 2 ;;\n"
        "    esac\n"
        "    if [ \"$value\" = 1 ] && [ \"${ZEROLAB_FAKE_SYSCTL_FAIL_SET:-0}\" = 1 ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    if [ -n \"${ZEROLAB_FAKE_SYSCTL_FAIL_VALUE:-}\" ] && [ \"$value\" = \"${ZEROLAB_FAKE_SYSCTL_FAIL_VALUE}\" ]; then\n"
        "      exit 1\n"
        "    fi\n"
        "    printf '%%s\\n' \"$value\" > \"$state\"\n"
        "    ;;\n"
        "  *)\n"
        "    exit 2\n"
        "    ;;\n"
        "esac\n" % (shlex.quote(str(log)), shlex.quote(str(arp_state))),
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
    address_state = tmp_path / "addresses"
    arp_state = tmp_path / "arp_ignore"
    ip = tmp_path / "ip"
    sysctl = tmp_path / "sysctl"
    notify = tmp_path / "systemd-notify"
    address_state.write_text("", encoding="utf-8")
    arp_state.write_text("0\n", encoding="utf-8")
    _fake_ip(ip, command_log, address_state)
    _fake_sysctl(sysctl, command_log, arp_state)
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
    return Fixture(
        env=env,
        command_log=command_log,
        notify_log=notify_log,
        address_state=address_state,
        arp_state=arp_state,
    )


def alias_fixture(tmp_path: Path) -> Fixture:
    fixture = make_fixture(tmp_path, mode="alias")
    fixture.env.update(
        {
            "ZEROLAB_ALIAS_IP": "192.168.50.27",
            "ZEROLAB_ETH": "enp-test",
            "ZEROLAB_WIFI": "wlan-test",
        }
    )
    return fixture


def commands(fixture: Fixture) -> list[str]:
    if not fixture.command_log.exists():
        return []
    return fixture.command_log.read_text(encoding="utf-8").splitlines()


def addresses(fixture: Fixture) -> set[str]:
    return set(fixture.address_state.read_text(encoding="utf-8").splitlines())


def set_addresses(fixture: Fixture, *entries: str) -> None:
    fixture.address_state.write_text(
        "".join(f"{entry}\n" for entry in entries), encoding="utf-8"
    )


def arp_ignore(fixture: Fixture) -> str:
    return fixture.arp_state.read_text(encoding="utf-8").strip()


def write_saved_state(fixture: Fixture, **overrides: str) -> None:
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    state.mkdir()
    values = {
        "schema": "1",
        "mode": "alias",
        "address": "192.168.50.27/32",
        "eth": "enp-test",
        "wifi": "wlan-test",
        "arp_ignore.old": "0",
        "address.origin": "added",
        "active": "1",
    }
    values.update(overrides)
    for name, value in values.items():
        (state / name).write_text(f"{value}\n", encoding="utf-8")


def saved_state(fixture: Fixture) -> dict[str, str]:
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    return {
        entry.name: entry.read_text(encoding="utf-8")
        for entry in state.iterdir()
    }


def expected_saved_state(arp_ignore_old: str, origin: str) -> dict[str, str]:
    return {
        "schema": "1\n",
        "mode": "alias\n",
        "address": "192.168.50.27/32\n",
        "eth": "enp-test\n",
        "wifi": "wlan-test\n",
        "arp_ignore.old": f"{arp_ignore_old}\n",
        "address.origin": f"{origin}\n",
        "active": "1\n",
    }


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


def test_state_directory_rejects_parent_traversal(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    fixture.env["ZEROLAB_NETWORK_STATE_DIR"] = "/tmp/../unsafe"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "state directory must be under /run/* or /tmp/*" in result.stderr
    assert commands(fixture) == []


def test_direct_run_notifies_ready_and_stays_active(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    process = start_helper(fixture, "run")
    assert wait_until(fixture.notify_log.exists)
    assert fixture.notify_log.read_text().splitlines() == ["--ready"]
    assert process.poll() is None
    process.terminate()
    assert process.wait(timeout=2) == 0
    assert not fixture.command_log.exists()


def test_alias_adds_custom_ip_as_exact_slash_32(tmp_path):
    fixture = alias_fixture(tmp_path)

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "1"
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    assert saved_state(fixture) == expected_saved_state("0", "added")
    assert state.stat().st_mode & 0o077 == 0
    assert all(entry.stat().st_mode & 0o077 == 0 for entry in state.iterdir())


def test_alias_rejects_cidr_input(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env["ZEROLAB_ALIAS_IP"] = "192.168.50.27/32"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_ALIAS_IP" in result.stderr
    assert commands(fixture) == []


def test_alias_rejects_out_of_range_ipv4(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env["ZEROLAB_ALIAS_IP"] = "256.1.1.1"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_ALIAS_IP" in result.stderr
    assert commands(fixture) == []


def test_alias_rejects_incomplete_ipv4(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env["ZEROLAB_ALIAS_IP"] = "1.2.3"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_ALIAS_IP" in result.stderr
    assert commands(fixture) == []


def test_alias_requires_ethernet_interface(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env.pop("ZEROLAB_ETH")

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_ETH" in result.stderr
    assert commands(fixture) == []


def test_alias_requires_wifi_interface(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env.pop("ZEROLAB_WIFI")

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_WIFI" in result.stderr
    assert commands(fixture) == []


def test_alias_rejects_unsafe_interface_name(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env["ZEROLAB_WIFI"] = "wlan0;reboot"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "invalid ZEROLAB_WIFI" in result.stderr
    assert commands(fixture) == []


def test_alias_sysctl_failure_removes_service_added_address(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.env["ZEROLAB_FAKE_SYSCTL_FAIL_SET"] = "1"
    fixture.arp_state.write_text("7\n", encoding="utf-8")

    result = run_helper(fixture, "start")

    assert result.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_retains_state_when_arp_restore_fails_then_retries(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0
    fixture.env["ZEROLAB_FAKE_SYSCTL_FAIL_VALUE"] = "7"

    failed_stop = run_helper(fixture, "stop")

    assert failed_stop.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "1"
    assert saved_state(fixture) == expected_saved_state("7", "added")

    fixture.env.pop("ZEROLAB_FAKE_SYSCTL_FAIL_VALUE")
    retry = run_helper(fixture, "stop")

    assert retry.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_retains_state_when_address_delete_fails_then_retries(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0
    fixture.env["ZEROLAB_FAKE_IP_FAIL_ACTION"] = "del"

    failed_stop = run_helper(fixture, "stop")

    assert failed_stop.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "7"
    assert saved_state(fixture) == expected_saved_state("7", "added")

    fixture.env.pop("ZEROLAB_FAKE_IP_FAIL_ACTION")
    retry = run_helper(fixture, "stop")

    assert retry.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_retains_state_when_preexisting_restore_fails_then_retries(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0
    set_addresses(fixture)
    fixture.env["ZEROLAB_FAKE_IP_FAIL_ACTION"] = "add"

    failed_stop = run_helper(fixture, "stop")

    assert failed_stop.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address add 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert saved_state(fixture) == expected_saved_state("7", "preexisting")

    fixture.env.pop("ZEROLAB_FAKE_IP_FAIL_ACTION")
    retry = run_helper(fixture, "stop")

    assert retry.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address add 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_retains_state_when_address_inspection_fails_then_retries(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0
    fixture.env["ZEROLAB_FAKE_IP_FAIL_ACTION"] = "show"

    failed_stop = run_helper(fixture, "stop")

    assert failed_stop.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "7"
    assert saved_state(fixture) == expected_saved_state("7", "added")

    fixture.env.pop("ZEROLAB_FAKE_IP_FAIL_ACTION")
    retry = run_helper(fixture, "stop")

    assert retry.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_removes_added_address_and_restores_original_arp(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0

    result = run_helper(fixture, "stop")

    assert result.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address del 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_preserves_preexisting_address(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0

    result = run_helper(fixture, "stop")

    assert result.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_stop_restores_preexisting_address_removed_externally(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    assert run_helper(fixture, "start").returncode == 0
    set_addresses(fixture)

    result = run_helper(fixture, "stop")

    assert result.returncode == 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=1",
        "sysctl -w net.ipv4.conf.wlan-test.arp_ignore=7",
        "ip -4 address show dev enp-test",
        "ip address add 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


@pytest.mark.parametrize(
    ("entry", "value"),
    [
        ("schema", "2"),
        ("mode", "direct"),
        ("address", "192.168.50.27/24"),
        ("eth", "enp-test;reboot"),
        ("wifi", "wlan-test;reboot"),
        ("arp_ignore.old", "-1"),
        ("address.origin", "unknown"),
        ("active", "0"),
        ("arp_ignore.old", "0\n1"),
    ],
)
def test_saved_state_values_are_validated_before_commands(tmp_path, entry, value):
    fixture = alias_fixture(tmp_path)
    write_saved_state(fixture, **{entry: value})

    result = run_helper(fixture, "stop")

    assert result.returncode != 0
    assert "invalid saved state" in result.stderr
    assert commands(fixture) == []
    assert Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


@pytest.mark.parametrize("kind", ["unknown", "missing", "symlink"])
def test_saved_state_rejects_unknown_incomplete_or_unsafe_entries(tmp_path, kind):
    fixture = alias_fixture(tmp_path)
    write_saved_state(fixture)
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    if kind == "unknown":
        (state / "unexpected").write_text("value\n", encoding="utf-8")
    elif kind == "missing":
        (state / "wifi").unlink()
    else:
        target = tmp_path / "symlink-target"
        target.write_text("enp-test\n", encoding="utf-8")
        (state / "eth").unlink()
        (state / "eth").symlink_to(target)

    result = run_helper(fixture, "stop")

    assert result.returncode != 0
    assert "invalid saved state" in result.stderr
    assert commands(fixture) == []
    assert state.exists()
