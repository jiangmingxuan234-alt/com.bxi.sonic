# ZeroLab Direct and Alias Network Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone Sonic network supervisor that defaults to non-mutating direct UDP reception and optionally manages a customer-configured `/32` alias without changing ZeroLab ARM behavior or blocking PD Brake/Normal.

**Architecture:** A systemd-notifying Bash supervisor reads an optional `/etc/default/zerolab-network` file and owns only runtime state it has explicitly recorded. Direct mode cleans proven service-owned alias state and otherwise performs no network operation; alias mode transactionally snapshots, applies, repairs, and restores a custom address and Wi-Fi `arp_ignore`. An optional hardware-service drop-in uses `Wants=`/`After=` rather than `Requires=`, so network errors remain visible without preventing the base controller from starting.

**Tech Stack:** Bash, systemd, Linux `ip`/`sysctl`, Python 3 and pytest fakes, ROS 2 Humble, colcon/CMake/CTest.

## Global Constraints

- `ZEROLAB_NETWORK_MODE` accepts exactly lowercase `direct` or `alias`; absent or empty means `direct`.
- Alias mode requires `ZEROLAB_ALIAS_IP`, `ZEROLAB_ETH`, and `ZEROLAB_WIFI`.
- `ZEROLAB_ALIAS_IP` is a plain IPv4 address; validate it and append `/32`.
- Direct mode makes no `ip` or `sysctl` call unless rolling back state proven to belong to a prior alias run.
- Alias mode reconciles its address and `arp_ignore=1` once per second by default.
- Alias stop restores the exact address-presence and numeric `arp_ignore` baseline.
- Saved values are never sourced as shell code and are validated before use.
- The hardware drop-in uses `Wants=`/`After=` and never `Requires=`.
- Network-service failure must not prevent the existing hardware controller, PD Brake, or Normal from starting.
- Keep `mod.yaml` unchanged: bind `0.0.0.0:18000`, allowed sender `192.168.89.171`.
- Keep A/Y, ARM/pause, `WAIT_STREAM`, `WAIT_ARM`, dropout recovery, ROS Domain, emergency behavior, and hardware launch commands unchanged.
- Do not modify `deploy_dependencies.sh`; it intentionally does not install systemd services.
- Do not stage or delete existing `build*`, `install*`, or `log*` directories.

## File map

- Create `deploy/zerolab-network-config`: configuration, runtime ownership, direct/alias lifecycle, repair, rollback.
- Create `deploy/config/zerolab-network`: installable default containing direct mode only.
- Create `deploy/systemd/zerolab-network.service`: notify supervisor unit.
- Create `deploy/systemd/zerolab-hardware.service.d/10-network.conf`: optional non-blocking ordering for an existing hardware unit.
- Create `tests/test_zerolab_network_config.py`: behavioral fake-command tests and static unit assertions.
- Create `deploy/README-zerolab-network.md`: install, switch, diagnose, and rollback instructions.
- Modify `README.md`: link to the deployment guide.

---

### Task 1: Direct-mode supervisor baseline

**Files:**
- Create: `deploy/zerolab-network-config`
- Create: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Consumes: `ZEROLAB_NETWORK_MODE`, `ZEROLAB_IP_BIN`, `ZEROLAB_SYSCTL_BIN`, `ZEROLAB_SYSTEMD_NOTIFY_BIN`, `ZEROLAB_NETWORK_STATE_DIR`, `ZEROLAB_RECONCILE_SECONDS`.
- Produces CLI: `deploy/zerolab-network-config {start|stop|run}`.
- Produces functions for later tasks: `validate_mode`, `cleanup_saved_state`, `start_network`, `stop_network`, `run_network`.

- [ ] **Step 1: Write the failing direct-mode fixture and tests**

Create a pytest fixture that writes fake `ip`, `sysctl`, and `systemd-notify` executables. Every fake writes its arguments to a log. The fixture must pass the fake paths through the environment and keep state under `tmp_path`.

Use these exact test names and assertions:

```python
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
```

- [ ] **Step 2: Run tests and confirm the missing-helper failure**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
```

Expected: FAIL because `deploy/zerolab-network-config` is absent.

- [ ] **Step 3: Implement minimal direct behavior**

Create executable `deploy/zerolab-network-config` with:

```bash
#!/bin/bash
set -eu

ACTION=${1:-}
MODE=${ZEROLAB_NETWORK_MODE:-direct}
IP_BIN=${ZEROLAB_IP_BIN:-/usr/sbin/ip}
SYSCTL_BIN=${ZEROLAB_SYSCTL_BIN:-/usr/sbin/sysctl}
SYSTEMD_NOTIFY_BIN=${ZEROLAB_SYSTEMD_NOTIFY_BIN:-/usr/bin/systemd-notify}
STATE_DIR=${ZEROLAB_NETWORK_STATE_DIR:-/run/zerolab-network}
RECONCILE_SECONDS=${ZEROLAB_RECONCILE_SECONDS:-1}
```

Implement the exact rules:

- `STATE_DIR` must be under `/run/*` or `/tmp/*`.
- `MODE` empty, unset, or `direct` selects direct; `alias` is recognized but returns `alias mode is not implemented` in this task.
- Every other mode exits `2` and prints `expected direct or alias`.
- Direct `start` and `stop` return `0` without invoking `ip` or `sysctl` when no state directory exists.
- `run` validates a positive reconciliation interval, calls `start`, sends `systemd-notify --ready`, traps `INT`/`TERM`, and remains active in a sleep loop.
- Unknown actions exit `2` with `usage: ... {start|stop|run}`.

- [ ] **Step 4: Run direct tests and syntax checks**

```bash
bash -n deploy/zerolab-network-config
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
```

Expected: shell syntax succeeds and all five tests pass.

- [ ] **Step 5: Commit the direct baseline**

```bash
git add deploy/zerolab-network-config tests/test_zerolab_network_config.py
git commit -m "feat: add direct ZeroLab network mode"
```

---

### Task 2: Transactional custom alias and exact rollback

**Files:**
- Modify: `deploy/zerolab-network-config`
- Modify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Consumes: `ZEROLAB_ALIAS_IP`, `ZEROLAB_ETH`, `ZEROLAB_WIFI`.
- Produces: `ALIAS_ADDRESS=<ZEROLAB_ALIAS_IP>/32`.
- Produces state entries: `schema`, `mode`, `address`, `eth`, `wifi`, `arp_ignore.old`, `address.origin`, `active`.
- Produces functions: `valid_ipv4`, `valid_interface`, `address_present`, `prepare_alias_state`, `start_alias`, `cleanup_saved_state`.

- [ ] **Step 1: Extend fakes and write failing alias tests**

Make fake `ip` maintain an address-state file and fake `sysctl` maintain a numeric ARP-state file. Add an `alias_fixture` with `ZEROLAB_ETH=enp-test` and `ZEROLAB_WIFI=wlan-test`.

Add these exact tests:

- `test_alias_adds_custom_ip_as_exact_slash_32`: `192.168.50.27` produces `ip address add 192.168.50.27/32 dev enp-test` and sets `net.ipv4.conf.wlan-test.arp_ignore=1`.
- `test_alias_rejects_cidr_input`: `192.168.50.27/32` exits `2` with `invalid ZEROLAB_ALIAS_IP`.
- `test_alias_rejects_out_of_range_ipv4`: reject `256.1.1.1`.
- `test_alias_rejects_incomplete_ipv4`: reject `1.2.3`.
- `test_alias_requires_ethernet_interface`: missing `ZEROLAB_ETH` exits `2`.
- `test_alias_requires_wifi_interface`: missing `ZEROLAB_WIFI` exits `2`.
- `test_alias_rejects_unsafe_interface_name`: reject `wlan0;reboot` before any fake command.
- `test_alias_sysctl_failure_removes_service_added_address`: a failed set-to-1 deletes the just-added address and restores the original numeric ARP value.
- `test_alias_stop_removes_added_address_and_restores_original_arp`.
- `test_alias_stop_preserves_preexisting_address`.
- `test_alias_stop_restores_preexisting_address_removed_externally`.
- `test_saved_state_values_are_validated_before_commands`.

Every rollback test must assert final fake network state and exact commands, not only return codes.

- [ ] **Step 2: Run alias tests and verify they fail**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py -k alias
```

Expected: FAIL because Task 1 rejects alias.

- [ ] **Step 3: Implement strict validation**

Implement:

```text
valid_ipv4(value)       exactly four decimal octets, each 0..255
valid_interface(value)  nonempty and only A-Z a-z 0-9 _ . : -
address_present(address, interface)
read_state_value(filename)  one nonempty line, no extra lines
write_state_value(directory, filename, value) under umask 077
```

Never `eval` or `source` configuration/state. Store `wifi`, validate it on cleanup, then derive `net.ipv4.conf.<wifi>.arp_ignore`; do not store a free-form sysctl key.

- [ ] **Step 4: Implement crash-safe state preparation and alias start**

Create a complete root-only `${STATE_DIR}.new.$$`, then atomically rename it to `STATE_DIR` before the first network mutation. Record:

```text
schema=1
mode=alias
address=<validated-ip>/32
eth=<validated-eth>
wifi=<validated-wifi>
arp_ignore.old=<original integer>
address.origin=added|preexisting
active=1
```

Write `address.origin=added` before attempting the add; an absent address is then an idempotent cleanup if the process dies before or during the add. Set `arp_ignore=1` only after the state is complete.

- [ ] **Step 5: Replace cleanup stub with exact idempotent rollback**

Cleanup must validate every saved entry before commands, restore the original numeric sysctl, delete an `added` address only when present, preserve or re-add a `preexisting` address, and retain any state entry whose rollback fails. Remove the state directory only after complete success. Unknown/incomplete saved state fails loudly and is never guessed away.

- [ ] **Step 6: Run all helper tests**

```bash
bash -n deploy/zerolab-network-config
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
```

Expected: all direct, alias, validation, and rollback tests pass.

- [ ] **Step 7: Commit transactional alias support**

```bash
git add deploy/zerolab-network-config tests/test_zerolab_network_config.py
git commit -m "feat: add transactional ZeroLab alias mode"
```

---

### Task 3: Alias reconciliation and safe mode/IP switching

**Files:**
- Modify: `deploy/zerolab-network-config`
- Modify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Consumes Task 2 saved state.
- Produces `reconcile_alias`, returning nonzero for the current repair failure without deleting the rollback snapshot.

- [ ] **Step 1: Write failing repair and transition tests**

Add these exact tests:

- `test_alias_run_repairs_removed_custom_address`.
- `test_alias_run_repairs_changed_arp_ignore`.
- `test_alias_run_termination_restores_baseline`.
- `test_reconcile_failure_retries_without_losing_snapshot`.
- `test_direct_start_cleans_service_owned_alias_state`.
- `test_direct_start_fails_when_owned_state_cleanup_is_incomplete`.
- `test_alias_ip_change_deletes_stored_old_ip_before_adding_new_ip`.
- `test_direct_never_deletes_address_without_owned_state`.

For the IP-change test, first save ownership of `192.168.50.27/32`, then request `10.22.33.44`. Assert delete-old occurs before add-new. For the unowned test, create an unrelated address with no `STATE_DIR` and assert no delete command.

- [ ] **Step 2: Run transition tests and verify failure**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py \
  -k 'repairs or owned_state or ip_change or unowned or termination'
```

Expected: FAIL because alias `run` does not reconcile.

- [ ] **Step 3: Implement reconciliation**

Implement `reconcile_alias` to reread and validate active saved state each cycle, add the exact saved address when absent, read the derived ARP key, and reset it to `1` when different. Log only a repair or failure. Never replace the baseline snapshot during reconciliation.

After readiness, run:

```bash
while :
do
  sleep "$RECONCILE_SECONDS"
  if [ "$MODE" = alias ] && ! reconcile_alias
  then
    echo "ZeroLab alias reconciliation failed; retrying" >&2
  fi
done
```

Direct only sleeps. Both signal cleanup and CLI stop use the same `cleanup_saved_state` path. Before applying a new mode/IP, clean existing valid owned state; if cleanup is incomplete, do not apply the new mode.

- [ ] **Step 4: Run the suite three times to catch leaks/races**

```bash
bash -n deploy/zerolab-network-config
for RUN in 1 2 3; do
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
    tests/test_zerolab_network_config.py || exit 1
done
```

Expected: three complete passes with no leaked helper process/state.

- [ ] **Step 5: Commit repair and switching**

```bash
git add deploy/zerolab-network-config tests/test_zerolab_network_config.py
git commit -m "fix: reconcile ZeroLab alias network state"
```

---

### Task 4: systemd packaging and non-blocking hardware ordering

**Files:**
- Create: `deploy/config/zerolab-network`
- Create: `deploy/systemd/zerolab-network.service`
- Create: `deploy/systemd/zerolab-hardware.service.d/10-network.conf`
- Modify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Install config at `/etc/default/zerolab-network`.
- Install helper at `/usr/local/libexec/zerolab-network-config`.
- Install unit at `/etc/systemd/system/zerolab-network.service`.
- Install drop-in at `/etc/systemd/system/zerolab-hardware.service.d/10-network.conf`.

- [ ] **Step 1: Write failing static packaging tests**

Add exact assertions:

```python
def test_packaged_default_is_direct():
    text = (ROOT / "deploy/config/zerolab-network").read_text()
    assert text.strip() == "ZEROLAB_NETWORK_MODE=direct"


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
```

- [ ] **Step 2: Run packaging tests and confirm missing-file failures**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py -k 'packaged or unit or drop_in'
```

Expected: FAIL because packaging files are absent.

- [ ] **Step 3: Create the exact configuration and units**

`deploy/config/zerolab-network`:

```bash
ZEROLAB_NETWORK_MODE=direct
```

`deploy/systemd/zerolab-network.service`:

```ini
[Unit]
Description=ZeroLab receive network configuration
Wants=network-online.target
After=NetworkManager.service network-online.target

[Service]
Type=notify
NotifyAccess=main
EnvironmentFile=-/etc/default/zerolab-network
Environment=ZEROLAB_IP_BIN=/usr/sbin/ip
Environment=ZEROLAB_SYSCTL_BIN=/usr/sbin/sysctl
Environment=ZEROLAB_SYSTEMD_NOTIFY_BIN=/usr/bin/systemd-notify
Environment=ZEROLAB_NETWORK_STATE_DIR=/run/zerolab-network
Environment=ZEROLAB_RECONCILE_SECONDS=1
ExecStart=/usr/local/libexec/zerolab-network-config run
Restart=on-failure
RestartSec=1s
TimeoutStopSec=10s

[Install]
WantedBy=multi-user.target
```

Hardware drop-in:

```ini
[Unit]
Wants=zerolab-network.service
After=zerolab-network.service
```

- [ ] **Step 4: Verify packaging and all tests**

```bash
systemd-analyze verify deploy/systemd/zerolab-network.service
bash -n deploy/zerolab-network-config
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
git diff --check
```

Expected: no unit-specific verification error, all tests pass, clean diff.

- [ ] **Step 5: Commit packaging**

```bash
git add \
  deploy/config/zerolab-network \
  deploy/systemd/zerolab-network.service \
  deploy/systemd/zerolab-hardware.service.d/10-network.conf \
  tests/test_zerolab_network_config.py
git commit -m "feat: package ZeroLab network services"
```

---

### Task 5: Deployment, diagnostics, switching, and rollback guide

**Files:**
- Create: `deploy/README-zerolab-network.md`
- Modify: `README.md`
- Modify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Produces copy/paste procedures without treating any test IP as a product default.

- [ ] **Step 1: Write a failing documentation contract**

Add `test_deployment_guide_covers_modes_safety_and_rollback`. Require the guide to contain:

```text
ZEROLAB_NETWORK_MODE=direct
ZEROLAB_NETWORK_MODE=alias
ZEROLAB_ALIAS_IP=
sudo systemctl restart zerolab-network.service
systemctl is-active zerolab-network.service
journalctl -u zerolab-network.service
WAIT_STREAM
WAIT_ARM
sudo systemctl stop zerolab-network.service
```

Assert the guide does not present `192.168.88.213` as a default and does not instruct direct users to edit `mod.yaml`.

- [ ] **Step 2: Run the contract and verify missing-guide failure**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py -k deployment_guide
```

Expected: FAIL because the guide is absent.

- [ ] **Step 3: Write the exact guide**

Document, in order:

1. back up existing config/helper/unit/drop-in;
2. install helper as `0755`, config/unit/drop-in as `0644`;
3. `systemd-analyze verify`, `daemon-reload`, enable and start;
4. prove default direct adds no address and changes no `arp_ignore`;
5. find the robot's existing IPv4 and target `<robot-ip>:18000`;
6. configure alias with a clearly marked customer example such as `10.22.33.44`;
7. verify exact `/32`, configured interface, `arp_ignore=1`, UDP, and journal;
8. switch to direct and prove only owned alias state is rolled back;
9. explain network failure leaves PD/Normal available while ZeroLab remains `WAIT_STREAM` and rejects Y until `WAIT_ARM`;
10. restore backups and remove only explicitly installed files.

Use `${VAR:?message}` for customer paths/IPs. Do not provide recursive wildcard deletion commands.

- [ ] **Step 4: Add a short root README link**

State only that direct is default, alias is explicit, and the full deployment guide is `deploy/README-zerolab-network.md`.

- [ ] **Step 5: Run docs/service verification and commit**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
git diff --check
git add README.md deploy/README-zerolab-network.md tests/test_zerolab_network_config.py
git commit -m "docs: explain ZeroLab network mode deployment"
```

Expected: all tests pass before the commit.

---

### Task 6: Full integration, Release build, and ELF3-81 acceptance

**Files:**
- Verify only; change prior files only when a failing test proves a defect.

**Interfaces:**
- Consumes: a clean host controller checkout through `HOST_WS` and BXI underlay through `BXI_ROS_PREFIX`.
- Produces: exact automated/build/hardware evidence.

- [ ] **Step 1: Run fresh standalone verification**

```bash
set -e
bash -n deploy/zerolab-network-config
systemd-analyze verify deploy/systemd/zerolab-network.service
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
python3 -c 'from pathlib import Path; files=[Path("plugin.py"),Path("policy.py"),Path("reference_gate.py"),Path("state.py")]+sorted(Path("zerolab").glob("*.py")); [compile(p.read_bytes(),str(p),"exec") for p in files]; print(f"PYTHON_COMPILE_OK={len(files)}")'
git diff --check sonic-upstream/main...HEAD
```

Expected: all service tests and compilation checks pass.

- [ ] **Step 2: Overlay this branch into a temporary host tree**

```bash
set -e
HOST_WS=${HOST_WS:?export HOST_WS to a clean bxi_controller_ros2 checkout}
VERIFY_ROOT=$(mktemp -d /tmp/zerolab-network-modes-verify.XXXXXX)
git -C "$HOST_WS" archive HEAD | tar -x -C "$VERIFY_ROOT"
mkdir -p "$VERIFY_ROOT/src/bxi_example_py_elf3/mods/com.bxi.sonic"
git archive HEAD | tar -x -C "$VERIFY_ROOT/src/bxi_example_py_elf3/mods/com.bxi.sonic"
printf 'VERIFY_ROOT=%s\n' "$VERIFY_ROOT"
```

Expected: the host source remains unchanged; only `/tmp` contains the overlay.

- [ ] **Step 3: Run the complete host Sonic/ZeroLab suite**

```bash
set -e
source /opt/ros/humble/setup.bash
BXI_ROS_PREFIX=${BXI_ROS_PREFIX:?export BXI_ROS_PREFIX to the BXI ROS underlay}
source "$BXI_ROS_PREFIX/setup.bash"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$VERIFY_ROOT/src/bxi_example_py_elf3:$VERIFY_ROOT/src/bxi_example_py_elf3/mods/com.bxi.sonic${PYTHONPATH:+:$PYTHONPATH}"
python3 -m pytest -q \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_sonic_ordered_playout.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_sonic_python_runtime.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_sonic_reference_gate.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_state_machine_inspector.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_arming.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_converter.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_lifecycle.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_manifest.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_pose_contract.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_protocol.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_recording.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_resampler.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_timeline.py" \
  "$VERIFY_ROOT/src/bxi_example_py_elf3/test/test_zerolab_udp_receiver.py"
```

Expected: all selected tests pass. Record the fresh count rather than quoting an earlier count.

- [ ] **Step 4: Run Release build and A/Y regression**

```bash
set -e
colcon --log-base "$VERIFY_ROOT/log-network-modes" build \
  --merge-install \
  --base-paths "$VERIFY_ROOT/src" \
  --packages-ignore bxi_depth_camera \
  --packages-select bxi_example_py_elf3 remote_controller \
  --allow-overriding bxi_example_py_elf3 remote_controller \
  --build-base "$VERIFY_ROOT/build-network-modes" \
  --install-base "$VERIFY_ROOT/install-network-modes" \
  --cmake-args -DCMAKE_BUILD_TYPE=Release
ctest --test-dir "$VERIFY_ROOT/build-network-modes/remote_controller" \
  -R remote_controller_control_rules_test --output-on-failure
```

Expected: both packages build and the focused A/Y test passes.

- [ ] **Step 5: Audit branch scope before any push**

```bash
git status --short --branch
git diff --name-status sonic-upstream/main...HEAD
git log --oneline sonic-upstream/main..HEAD
git diff --check sonic-upstream/main...HEAD
```

Only these tracked paths may appear:

```text
README.md
deploy/README-zerolab-network.md
deploy/config/zerolab-network
deploy/systemd/zerolab-hardware.service.d/10-network.conf
deploy/systemd/zerolab-network.service
deploy/zerolab-network-config
docs/superpowers/plans/2026-09-01-zerolab-network-modes.md
docs/superpowers/specs/2026-09-01-zerolab-network-modes-design.md
tests/test_zerolab_network_config.py
```

No `mod.yaml`, A/Y source, hardware launch command, or build/install/log output may appear.

- [ ] **Step 6: Validate ELF3-81 direct under hardware safety controls**

After explicit operator confirmation, reliable support, physical emergency stop, and safety observer:

1. stop the old network service cleanly and back it up;
2. install the new helper/unit/direct config/drop-in;
3. record interface addresses and `arp_ignore` before and after direct start, proving no change;
4. target the sender at the robot's current Ethernet IPv4 on port `18000`;
5. verify strict UDP, `WAIT_STREAM -> WAIT_ARM`, explicit Y ARM, Y pause, PD Brake, and Normal;
6. with UDP absent, verify A stays `WAIT_STREAM` and Y is rejected.

Do not send Y until the latest log explicitly shows `WAIT_ARM` and every existing hardware prerequisite is satisfied.

- [ ] **Step 7: Validate custom alias and return ELF3-81 to direct**

1. configure the actual bridge IP chosen for the test and restart;
2. verify exact custom `/32`, configured `arp_ignore=1`, strict UDP, and unchanged control behavior;
3. delete only that `/32` and change only that ARP setting, then verify repair within two seconds;
4. switch back to direct and prove the owned alias/sysctl baseline is restored;
5. leave ELF3-81 in direct and archive the exact journal/test output.

- [ ] **Step 8: Finish the branch**

Invoke `superpowers:finishing-a-development-branch`. Do not push PR #2 until automated/build checks and direct/alias evidence exist, unless the user explicitly chooses code review before robot validation.

## Completion checklist

- [ ] Each implementation task has a red-green cycle and separate commit.
- [ ] Fresh direct makes zero network mutation.
- [ ] Alias uses the configured IPv4 plus `/32`.
- [ ] Rollback is validated, idempotent, exact, and retryable.
- [ ] Alias repairs drift without losing its baseline.
- [ ] Invalid network configuration is explicit but does not block hardware/PD/Normal.
- [ ] Existing stream and ARM gates remain unchanged.
- [ ] Standalone tests, host tests, Release build, and A/Y regression pass.
- [ ] ELF3-81 validates both modes and finishes in direct.
- [ ] Final branch contains no local paths or build/install/log artifacts.
