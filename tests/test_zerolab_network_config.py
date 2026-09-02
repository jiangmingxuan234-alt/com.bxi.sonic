from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "zerolab-network-config"


def test_packaged_default_is_direct():
    text = (ROOT / "deploy/config/zerolab-network").read_text()
    assert text.strip() == "ZEROLAB_NETWORK_MODE=direct"


def test_deployment_guide_covers_modes_safety_and_rollback():
    text = (ROOT / "deploy/README-zerolab-network.md").read_text(
        encoding="utf-8"
    )

    for required_text in [
        "ZEROLAB_NETWORK_MODE=direct",
        "ZEROLAB_NETWORK_MODE=alias",
        "ZEROLAB_ALIAS_IP=",
        "sudo systemctl restart zerolab-network.service",
        "systemctl is-active zerolab-network.service",
        "journalctl -u zerolab-network.service",
        "WAIT_STREAM",
        "WAIT_ARM",
        "sudo systemctl stop zerolab-network.service",
    ]:
        assert required_text in text

    assert "192.168.88.213" not in text

    direct_section = text.split("## Alias mode", maxsplit=1)[0]
    assert "mod.yaml" not in direct_section

    def first_shell_block_after(marker: str) -> str:
        after_marker = text.split(marker, maxsplit=1)[1]
        return after_marker.split("~~~bash", maxsplit=1)[1].split(
            "~~~", maxsplit=1
        )[0]

    for marker in [
        "## Back up and install",
        "## Direct mode",
        "Write the explicit alias configuration",
        "## Switch back to direct",
        "### Stop the network service",
        "## Restore the backup",
    ]:
        assert "set -Eeuo pipefail" in first_shell_block_after(marker)

    for required_text in [
        'sudo mkdir -- "$BACKUP_DIR"',
        "snapshot_service_state()",
        "systemctl is-enabled zerolab-network.service",
        "systemctl is-active zerolab-network.service",
        "$BACKUP_DIR/zerolab-network.enabled",
        "$BACKUP_DIR/zerolab-network.active",
        "restore_service_state()",
        "sudo systemctl enable zerolab-network.service",
        "sudo systemctl disable zerolab-network.service",
        "sudo systemctl start zerolab-network.service",
        'sudo cp -a -- "$BACKUP_DIR/$backup_name" "$target_file"',
    ]:
        assert required_text in text
    assert 'install -d -m 0700 "$BACKUP_DIR"' not in text

    rollback_section = text.split("## Restore the backup", maxsplit=1)[1]
    assert rollback_section.index("sudo systemctl stop zerolab-network.service") < rollback_section.index(
        "sudo cp -a"
    )
    assert rollback_section.index("sudo systemctl stop zerolab-network.service") < rollback_section.index(
        "sudo rm -f"
    )
    assert rollback_section.index("sudo systemctl daemon-reload") < (
        rollback_section.index("restore_service_state")
    )


def test_deployment_setup_refuses_existing_backup_dir_without_overwriting_manifest(
    tmp_path,
):
    text = (ROOT / "deploy/README-zerolab-network.md").read_text(
        encoding="utf-8"
    )
    setup_block = text.split("## Back up and install", maxsplit=1)[1].split(
        "~~~bash", maxsplit=1
    )[1].split("~~~", maxsplit=1)[0].split("snapshot_service_state()", maxsplit=1)[
        0
    ]
    assert 'sudo mkdir -- "$BACKUP_DIR"' in setup_block
    assert 'install -d -m 0700 "$BACKUP_DIR"' not in setup_block

    repo_root = tmp_path / "repo"
    for relative_path in [
        "deploy/zerolab-network-config",
        "deploy/config/zerolab-network",
        "deploy/systemd/zerolab-network.service",
        "deploy/systemd/zerolab-hardware.service.d/10-network.conf",
    ]:
        path = repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    backup_dir = tmp_path / "existing-backup"
    backup_dir.mkdir()
    manifest = backup_dir / "present"
    manifest.write_text("keep-this-manifest\n", encoding="utf-8")
    fake_sudo = tmp_path / "sudo"
    fake_sudo.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    fake_sudo.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "REPO_ROOT": str(repo_root),
            "BACKUP_DIR": str(backup_dir),
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", "-c", setup_block],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert manifest.read_text(encoding="utf-8") == "keep-this-manifest\n"


def test_network_unit_uses_optional_config_and_notify_supervisor():
    text = (ROOT / "deploy/systemd/zerolab-network.service").read_text()
    assert "Type=notify" in text
    assert "NotifyAccess=main" in text
    assert "EnvironmentFile=-/etc/default/zerolab-network" in text
    assert "ExecStart=/usr/local/libexec/zerolab-network-config run" in text
    assert "Restart=on-failure" in text
    assert "RemainAfterExit=" not in text
    assert "ExecStartPre=/usr/bin/test -d /sys/class/net/" not in text


def test_hardware_drop_in_keeps_network_non_blocking():
    text = (
        ROOT / "deploy/systemd/zerolab-hardware.service.d/10-network.conf"
    ).read_text()
    assert "Wants=zerolab-network.service" in text
    assert "After=zerolab-network.service" in text
    assert "Requires=zerolab-network.service" not in text
    assert "ExecStart" not in text


def test_network_unit_verifies_with_staged_helper(tmp_path):
    systemd_analyze = shutil.which("systemd-analyze")
    bwrap = shutil.which("bwrap")
    if systemd_analyze is None or bwrap is None:
        pytest.skip("systemd-analyze and bwrap are required")

    staged_helper = tmp_path / "usr/local/libexec/zerolab-network-config"
    staged_helper.parent.mkdir(parents=True)
    shutil.copy2(HELPER, staged_helper)
    staged_helper.chmod(0o755)

    result = subprocess.run(
        [
            bwrap,
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/usr/local",
            "--dir",
            "/usr/local/libexec",
            "--ro-bind",
            str(staged_helper),
            "/usr/local/libexec/zerolab-network-config",
            systemd_analyze,
            "verify",
            str(ROOT / "deploy/systemd/zerolab-network.service"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@dataclass
class Fixture:
    env: dict[str, str]
    command_log: Path
    notify_log: Path
    address_state: Path
    ordinary_address_state: Path
    carrier_state: Path
    arp_state: Path
    ip_failure_control: Path
    ip_failure_log: Path
    ip_success_log: Path


def _fake_ip(
    path: Path,
    log: Path,
    address_state: Path,
    ordinary_address_state: Path,
    failure_log: Path,
    success_log: Path,
) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'ip %%s\\n' \"$*\" >> %s\n"
        "state=%s\n"
        "failure_log=%s\n"
        "success_log=%s\n"
        "fail_action=${ZEROLAB_FAKE_IP_FAIL_ACTION:-}\n"
        "if [ -f \"${ZEROLAB_FAKE_IP_FAIL_ACTION_FILE:-}\" ]; then\n"
        "  fail_action=$(cat \"$ZEROLAB_FAKE_IP_FAIL_ACTION_FILE\")\n"
        "fi\n"
        "case \"$*\" in\n"
        "  '-4 address show dev '*)\n"
        "    if [ \"$fail_action\" = show ]; then\n"
        "      printf 'show\\n' >> \"$failure_log\"\n"
        "      exit 1\n"
        "    fi\n"
        "    interface=$5\n"
        "    while IFS=' ' read -r address entry_interface; do\n"
        "      if [ \"$entry_interface\" = \"$interface\" ]; then\n"
        "        printf '    inet %%s scope global\\n' \"$address\"\n"
        "      fi\n"
        "    done < %s\n"
        "    while IFS=' ' read -r address entry_interface; do\n"
        "      if [ \"$entry_interface\" = \"$interface\" ]; then\n"
        "        printf '    inet %%s scope global\\n' \"$address\"\n"
        "      fi\n"
        "    done < \"$state\"\n"
        "    printf 'show\\n' >> \"$success_log\"\n"
        "    ;;\n"
        "  'address add '*)\n"
        "    if [ \"${ZEROLAB_FAKE_IP_FAIL_ADD_AFTER_ADD:-0}\" = 1 ]; then\n"
        "      address=$3\n"
        "      interface=$5\n"
        "      printf '%%s %%s\\n' \"$address\" \"$interface\" >> \"$state\"\n"
        "      exit 1\n"
        "    fi\n"
        "    if [ \"$fail_action\" = add ]; then\n"
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
        "    if [ \"$fail_action\" = del ]; then\n"
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
        "esac\n"
        % (
            shlex.quote(str(log)),
            shlex.quote(str(address_state)),
            shlex.quote(str(failure_log)),
            shlex.quote(str(success_log)),
            shlex.quote(str(ordinary_address_state)),
        ),
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
    ordinary_address_state = tmp_path / "ordinary-addresses"
    sys_class_net = tmp_path / "sys-class-net"
    carrier_state = sys_class_net / "enp-test" / "carrier"
    arp_state = tmp_path / "arp_ignore"
    ip_failure_control = tmp_path / "ip-failure-action"
    ip_failure_log = tmp_path / "ip-failures.log"
    ip_success_log = tmp_path / "ip-successes.log"
    ip = tmp_path / "ip"
    sysctl = tmp_path / "sysctl"
    notify = tmp_path / "systemd-notify"
    address_state.write_text("", encoding="utf-8")
    carrier_state.parent.mkdir(parents=True)
    carrier_state.write_text("1\n", encoding="utf-8")
    ordinary_address_state.write_text("10.0.0.10/24 enp-test\n", encoding="utf-8")
    arp_state.write_text("0\n", encoding="utf-8")
    _fake_ip(
        ip,
        command_log,
        address_state,
        ordinary_address_state,
        ip_failure_log,
        ip_success_log,
    )
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
            "ZEROLAB_SYS_CLASS_NET_ROOT": str(sys_class_net),
            "ZEROLAB_RECONCILE_SECONDS": "0.01",
            "ZEROLAB_FAKE_IP_FAIL_ACTION_FILE": str(ip_failure_control),
        }
    )
    return Fixture(
        env=env,
        command_log=command_log,
        notify_log=notify_log,
        address_state=address_state,
        ordinary_address_state=ordinary_address_state,
        carrier_state=carrier_state,
        arp_state=arp_state,
        ip_failure_control=ip_failure_control,
        ip_failure_log=ip_failure_log,
        ip_success_log=ip_success_log,
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


def set_ordinary_addresses(fixture: Fixture, *entries: str) -> None:
    text = "".join(f"{entry}\n" for entry in entries)
    fixture.ordinary_address_state.write_text(text, encoding="utf-8")


def set_carrier(fixture: Fixture, up: bool) -> None:
    fixture.carrier_state.write_text("1\n" if up else "0\n", encoding="utf-8")


def arp_ignore(fixture: Fixture) -> str:
    return fixture.arp_state.read_text(encoding="utf-8").strip()


def write_saved_state(fixture: Fixture, **overrides: str) -> None:
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    state.mkdir()
    state.chmod(0o700)
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
        entry = state / name
        entry.write_text(f"{value}\n", encoding="utf-8")
        entry.chmod(0o600)


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
    fixture.carrier_state.unlink()
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


def test_state_directory_rejects_symlinked_ancestor(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    escaped_parent = tmp_path / "escaped-parent"
    escaped_parent.symlink_to("/outside-zerolab-state")
    fixture.env["ZEROLAB_NETWORK_STATE_DIR"] = str(escaped_parent / "state")

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "state directory must be under /run/* or /tmp/*" in result.stderr
    assert commands(fixture) == []


def test_state_directory_rejects_symlinked_final_component(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    state_link = tmp_path / "state-link"
    state_link.symlink_to(tmp_path / "redirected-state")
    fixture.env["ZEROLAB_NETWORK_STATE_DIR"] = str(state_link)

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "state directory must be under /run/* or /tmp/*" in result.stderr
    assert commands(fixture) == []


def test_state_directory_rejects_symlinked_final_component_with_trailing_slash(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    state_link = tmp_path / "state-link"
    state_link.symlink_to(tmp_path / "redirected-state")
    fixture.env["ZEROLAB_NETWORK_STATE_DIR"] = f"{state_link}/"

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "state directory must be under /run/* or /tmp/*" in result.stderr
    assert commands(fixture) == []


def test_state_directory_rejects_group_writable_ancestor_before_commands(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    writable_parent = tmp_path / "writable-parent"
    writable_parent.mkdir()
    writable_parent.chmod(0o777)
    fixture.env["ZEROLAB_NETWORK_STATE_DIR"] = str(writable_parent / "state")

    result = run_helper(fixture, "start")

    assert result.returncode == 2
    assert "state directory is not trusted" in result.stderr
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


def test_alias_start_waits_with_carrier_down(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_carrier(fixture, False)

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "1"
    assert saved_state(fixture) == expected_saved_state("0", "added")
    assert "waiting for ordinary Ethernet network" in result.stderr


def test_alias_start_waits_without_ordinary_ipv4(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_ordinary_addresses(fixture)

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "1"
    assert saved_state(fixture) == expected_saved_state("0", "added")


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


@pytest.mark.parametrize(
    ("target", "mode"),
    [("directory", 0o755), ("file", 0o640)],
)
def test_saved_state_rejects_nonprivate_permissions_before_commands(
    tmp_path, target, mode
):
    fixture = alias_fixture(tmp_path)
    write_saved_state(fixture)
    state = Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"])
    if target == "directory":
        state.chmod(mode)
    else:
        (state / "address").chmod(mode)

    result = run_helper(fixture, "stop")

    assert result.returncode != 0
    expected_error = (
        "invalid saved state: state path is not trusted"
        if target == "directory"
        else "invalid saved state: unsafe entry address"
    )
    assert expected_error in result.stderr
    assert commands(fixture) == []
    assert state.exists()


def test_alias_failed_add_does_not_delete_address_that_appears_during_failure(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    fixture.env["ZEROLAB_FAKE_IP_FAIL_ADD_AFTER_ADD"] = "1"

    result = run_helper(fixture, "start")

    assert result.returncode != 0
    assert commands(fixture) == [
        "ip -4 address show dev enp-test",
        "sysctl -n net.ipv4.conf.wlan-test.arp_ignore",
        "ip address add 192.168.50.27/32 dev enp-test",
    ]
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
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


def terminate_helper(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
    return process.communicate(timeout=2)


def test_alias_run_repairs_removed_custom_address(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: fixture.notify_log.exists()
            and addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        set_addresses(fixture)

        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_withdraws_until_ordinary_network_recovers(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        snapshot = saved_state(fixture)

        set_carrier(fixture, False)
        set_ordinary_addresses(fixture)
        assert wait_until(lambda: addresses(fixture) == set())
        assert saved_state(fixture) == snapshot

        set_carrier(fixture, True)
        set_ordinary_addresses(fixture, "10.0.0.11/24 enp-test")
        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        assert saved_state(fixture) == snapshot
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_never_withdraws_preexisting_alias(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    process = start_helper(fixture, "run")

    try:
        assert wait_until(fixture.notify_log.exists)
        set_carrier(fixture, False)
        set_ordinary_addresses(fixture)
        time.sleep(0.05)
        assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
        assert saved_state(fixture)["address.origin"] == "preexisting\n"
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_does_not_mutate_address_when_carrier_is_unreadable(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        fixture.carrier_state.write_text("unknown\n", encoding="utf-8")
        time.sleep(0.05)
        assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    finally:
        fixture.carrier_state.write_text("1\n", encoding="utf-8")
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_does_not_mutate_address_when_address_inspection_fails(
    tmp_path,
):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: fixture.notify_log.exists()
            and addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        snapshot = saved_state(fixture)
        command_boundary = len(commands(fixture))
        failure_boundary = (
            len(fixture.ip_failure_log.read_text(encoding="utf-8").splitlines())
            if fixture.ip_failure_log.exists()
            else 0
        )
        fixture.ip_failure_control.write_text("show\n", encoding="utf-8")

        assert wait_until(
            lambda: fixture.ip_failure_log.exists()
            and fixture.ip_failure_log.read_text(encoding="utf-8")
            .splitlines()
            .count("show")
            >= failure_boundary + 2
        )
        assert not any(
            command.startswith("ip address add ")
            or command.startswith("ip address del ")
            for command in commands(fixture)[command_boundary:]
        )
        assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
        assert saved_state(fixture) == snapshot

        success_boundary = (
            len(fixture.ip_success_log.read_text(encoding="utf-8").splitlines())
            if fixture.ip_success_log.exists()
            else 0
        )
        fixture.ip_failure_control.unlink()
        assert wait_until(
            lambda: fixture.ip_success_log.exists()
            and fixture.ip_success_log.read_text(encoding="utf-8")
            .splitlines()
            .count("show")
            > success_boundary
        )
        fixture.arp_state.write_text("7\n", encoding="utf-8")
        assert wait_until(lambda: arp_ignore(fixture) == "1")
    finally:
        if fixture.ip_failure_control.exists():
            fixture.ip_failure_control.unlink()
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_retries_failed_withdrawal_without_losing_snapshot(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        snapshot = saved_state(fixture)
        set_carrier(fixture, False)
        set_ordinary_addresses(fixture)
        fixture.ip_failure_control.write_text("del\n", encoding="utf-8")

        assert wait_until(
            lambda: commands(fixture).count(
                "ip address del 192.168.50.27/32 dev enp-test"
            ) >= 3
        )
        assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
        assert saved_state(fixture) == snapshot

        fixture.ip_failure_control.unlink()
        assert wait_until(lambda: addresses(fixture) == set())
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_stop_defers_missing_preexisting_alias_until_ordinary_network_recovers(
    tmp_path,
):
    fixture = alias_fixture(tmp_path)
    write_saved_state(fixture, **{"address.origin": "preexisting"})
    set_carrier(fixture, False)
    set_ordinary_addresses(fixture)
    snapshot = saved_state(fixture)

    deferred_stop = run_helper(fixture, "stop")

    assert deferred_stop.returncode != 0
    assert "preexisting alias restoration deferred until ordinary Ethernet is ready" in (
        deferred_stop.stderr
    )
    assert addresses(fixture) == set()
    assert saved_state(fixture) == snapshot

    set_carrier(fixture, True)
    set_ordinary_addresses(fixture, "10.0.0.11/24 enp-test")
    restored_stop = run_helper(fixture, "stop")

    assert restored_stop.returncode == 0
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_alias_run_waits_with_carrier_down(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_carrier(fixture, False)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(fixture.notify_log.exists)
        assert wait_until(
            lambda: commands(fixture).count(
                "ip -4 address show dev enp-test"
            )
            >= 2
        )
        time.sleep(0.05)
        assert fixture.notify_log.read_text().splitlines() == ["--ready"]
        assert addresses(fixture) == set()
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_waits_without_ordinary_ipv4(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_ordinary_addresses(fixture)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(fixture.notify_log.exists)
        assert wait_until(
            lambda: commands(fixture).count(
                "ip -4 address show dev enp-test"
            )
            >= 2
        )
        time.sleep(0.05)
        assert fixture.notify_log.read_text().splitlines() == ["--ready"]
        assert addresses(fixture) == set()
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_reconciles_arp_when_carrier_down_and_alias_missing(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_carrier(fixture, False)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(fixture.notify_log.exists)
        assert wait_until(
            lambda: commands(fixture).count(
                "ip -4 address show dev enp-test"
            )
            >= 2
        )
        assert addresses(fixture) == set()
        fixture.arp_state.write_text("7\n", encoding="utf-8")

        assert wait_until(lambda: arp_ignore(fixture) == "1")
        assert addresses(fixture) == set()
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_repairs_changed_arp_ignore(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: fixture.notify_log.exists() and arp_ignore(fixture) == "1"
        )
        fixture.arp_state.write_text("7\n", encoding="utf-8")

        assert wait_until(lambda: arp_ignore(fixture) == "1")
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_alias_run_termination_restores_baseline(tmp_path):
    fixture = alias_fixture(tmp_path)
    fixture.arp_state.write_text("7\n", encoding="utf-8")
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: fixture.notify_log.exists()
            and addresses(fixture) == {"192.168.50.27/32 enp-test"}
            and arp_ignore(fixture) == "1"
        )
    finally:
        terminate_helper(process)

    assert process.returncode == 0
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "7"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_reconcile_failure_retries_without_losing_snapshot(tmp_path):
    fixture = alias_fixture(tmp_path)
    process = start_helper(fixture, "run")

    try:
        assert wait_until(
            lambda: fixture.notify_log.exists()
            and addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
        snapshot = saved_state(fixture)
        fixture.ip_failure_control.write_text("add\n", encoding="utf-8")
        set_addresses(fixture)

        assert wait_until(
            lambda: commands(fixture).count(
                "ip address add 192.168.50.27/32 dev enp-test"
            )
            >= 3
        )
        assert addresses(fixture) == set()
        assert saved_state(fixture) == snapshot

        fixture.ip_failure_control.unlink()
        assert wait_until(
            lambda: addresses(fixture) == {"192.168.50.27/32 enp-test"}
        )
    finally:
        terminate_helper(process)

    assert process.returncode == 0


def test_direct_start_cleans_service_owned_alias_state(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    fixture.arp_state.write_text("1\n", encoding="utf-8")
    write_saved_state(fixture)
    fixture.env["ZEROLAB_NETWORK_MODE"] = "direct"

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "0"
    assert not Path(fixture.env["ZEROLAB_NETWORK_STATE_DIR"]).exists()


def test_direct_start_fails_when_owned_state_cleanup_is_incomplete(tmp_path):
    fixture = alias_fixture(tmp_path)
    set_addresses(fixture, "192.168.50.27/32 enp-test")
    fixture.arp_state.write_text("1\n", encoding="utf-8")
    write_saved_state(fixture)
    fixture.env["ZEROLAB_NETWORK_MODE"] = "direct"
    fixture.env["ZEROLAB_FAKE_SYSCTL_FAIL_VALUE"] = "0"

    result = run_helper(fixture, "start")

    assert result.returncode != 0
    assert addresses(fixture) == set()
    assert arp_ignore(fixture) == "1"
    assert saved_state(fixture) == expected_saved_state("0", "added")


def test_alias_ip_change_deletes_stored_old_ip_before_adding_new_ip(tmp_path):
    fixture = alias_fixture(tmp_path)

    assert run_helper(fixture, "start").returncode == 0
    fixture.env["ZEROLAB_ALIAS_IP"] = "10.22.33.44"

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    log = commands(fixture)
    assert log.index("ip address del 192.168.50.27/32 dev enp-test") < log.index(
        "ip address add 10.22.33.44/32 dev enp-test"
    )
    assert addresses(fixture) == {"10.22.33.44/32 enp-test"}
    assert run_helper(fixture, "stop").returncode == 0


def test_direct_never_deletes_address_without_owned_state(tmp_path):
    fixture = make_fixture(tmp_path, mode="direct")
    set_addresses(fixture, "192.168.50.27/32 enp-test")

    result = run_helper(fixture, "start")

    assert result.returncode == 0
    assert addresses(fixture) == {"192.168.50.27/32 enp-test"}
    assert commands(fixture) == []
