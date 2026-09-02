# ZeroLab Alias Link-Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent ZeroLab alias reconciliation from taking over a recovering Ethernet interface, while restoring the service-owned `/32` automatically after the ordinary IPv4 network returns.

**Architecture:** Extend the existing shell helper with a small, NetworkManager-independent readiness check: the configured Ethernet interface must have carrier and an ordinary IPv4 address other than the ZeroLab alias. Alias startup may enter a safe waiting state, reconciliation withdraws a service-owned alias while the ordinary network is unavailable, and the existing ownership snapshot drives later restoration and exact cleanup.

**Tech Stack:** Bash, Linux `ip`/sysfs interface state, systemd notify service, Python 3, pytest, ROS 2/colcon, CTest.

## Global Constraints

- Default mode remains `direct` and must not inspect or mutate network state.
- Do not query, create, edit, delete, or activate NetworkManager profiles.
- Do not own, delete, or rewrite the customer's ordinary Ethernet IPv4 address.
- Alias readiness requires carrier plus an ordinary IPv4 address other than the configured `/32`.
- Alias-only managed Ethernet interfaces without an ordinary IPv4 address are out of scope.
- Preserve the existing saved-state schema and exact ownership rollback rules.
- Keep sender allowlist `192.168.89.171`, UDP port `18000`, ROS domain, PD/Normal, A/Y, ARM, pause, and emergency behavior unchanged.
- A configured-but-waiting alias service must still notify systemd ready; stream freshness continues to block ARM.
- Initial address-inspection failure remains fatal because alias ownership
  cannot be classified safely; later inspection uncertainty never mutates an
  address and is retried against the existing trusted snapshot.
- Do not stage or delete any existing untracked `build*`, `install*`, or `log*` directories.

---

## File Map

- Modify `deploy/zerolab-network-config`: inspect carrier and ordinary IPv4 state; gate startup, reconciliation, and preexisting-address restoration.
- Modify `tests/test_zerolab_network_config.py`: model carrier and ordinary IPv4 separately from the service-owned alias; cover loss, withdrawal, recovery, retry, and direct no-op behavior.
- Modify `deploy/README-zerolab-network.md`: document the ordinary-address prerequisite, waiting state, reconnect behavior, and diagnostics.
- Modify `tests/test_zerolab_network_config.py`: assert that the deployment guide contains the new operational contract.
- Use `docs/superpowers/specs/2026-09-02-zerolab-alias-link-recovery-design.md` as the accepted source of requirements; do not edit it during implementation.

---

### Task 1: Model Interface Readiness and Gate Alias Startup

**Files:**
- Modify: `tests/test_zerolab_network_config.py:200-360,540-590`
- Modify: `deploy/zerolab-network-config:4-14,224-240,361-431,635-670`

**Interfaces:**
- Produces test fixture fields `ordinary_address_state: Path` and `carrier_state: Path`.
- Produces test helpers `set_ordinary_addresses(fixture, *entries)` and `set_carrier(fixture, up: bool)`.
- Produces shell globals `INSPECTED_ALIAS_PRESENT` and `INSPECTED_ORDINARY_IPV4_PRESENT` with values `0` or `1`.
- Produces `inspect_interface_addresses ADDRESS INTERFACE`, returning `0` on a valid inspection and `2` when `ip` inspection fails.
- Produces `alias_interface_ready INTERFACE`, returning `0` only for carrier `1` plus `INSPECTED_ORDINARY_IPV4_PRESENT=1`, `1` for a known unready interface, and `2` for an unreadable carrier state.

- [ ] **Step 1: Extend the fake network model without changing alias ownership assertions**

Add two paths to `Fixture`:

```python
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
```

Change `_fake_ip` to accept `ordinary_address_state`. For `-4 address show dev`, print matching ordinary entries first, then the existing mutable alias entries. Address add/delete must continue to mutate only `address_state`, so existing `addresses(fixture)` ownership assertions remain focused on ZeroLab-owned state.

Create a fake sysfs tree in `make_fixture`:

```python
sys_class_net = tmp_path / "sys-class-net"
carrier_state = sys_class_net / "enp-test" / "carrier"
carrier_state.parent.mkdir(parents=True)
carrier_state.write_text("1\n", encoding="utf-8")
ordinary_address_state.write_text("10.0.0.10/24 enp-test\n", encoding="utf-8")
fixture.env["ZEROLAB_SYS_CLASS_NET_ROOT"] = str(sys_class_net)
```

Add deterministic setters:

```python
def set_ordinary_addresses(fixture: Fixture, *entries: str) -> None:
    text = "".join(f"{entry}\n" for entry in entries)
    fixture.ordinary_address_state.write_text(text, encoding="utf-8")


def set_carrier(fixture: Fixture, up: bool) -> None:
    fixture.carrier_state.write_text("1\n" if up else "0\n", encoding="utf-8")
```

- [ ] **Step 2: Write startup tests that expose the race**

Keep `test_alias_adds_custom_ip_as_exact_slash_32` as the ready-interface case. Add:

```python
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
```

Add a direct-mode assertion that no carrier file is read by deleting the fake carrier file before `run_helper(fixture, "start")`; direct must still return zero with an empty command log.

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```bash
pytest -q \
  tests/test_zerolab_network_config.py::test_alias_adds_custom_ip_as_exact_slash_32 \
  tests/test_zerolab_network_config.py::test_alias_start_waits_with_carrier_down \
  tests/test_zerolab_network_config.py::test_alias_start_waits_without_ordinary_ipv4 \
  tests/test_zerolab_network_config.py::test_direct_never_deletes_address_without_owned_state
```

Expected: the two waiting tests fail because current startup adds the `/32` unconditionally.

- [ ] **Step 4: Implement address inspection and readiness primitives**

Add the configurable sysfs root next to `IP_BIN`:

```bash
SYS_CLASS_NET_ROOT=${ZEROLAB_SYS_CLASS_NET_ROOT:-/sys/class/net}
INSPECTED_ALIAS_PRESENT=0
INSPECTED_ORDINARY_IPV4_PRESENT=0
```

Replace the internals of `address_present` with a shared inspector while preserving its public return contract:

```bash
inspect_interface_addresses() {
    local address=$1
    local interface=$2
    local output

    valid_address "$address" && valid_interface "$interface" || return 2
    if ! output=$("$IP_BIN" -4 address show dev "$interface"); then
        printf 'failed to inspect saved alias address\n' >&2
        return 2
    fi
    INSPECTED_ALIAS_PRESENT=0
    INSPECTED_ORDINARY_IPV4_PRESENT=0
    if printf '%s\n' "$output" | awk -v address="$address" \
        '$1 == "inet" && $2 == address { found = 1 } END { exit !found }'
    then
        INSPECTED_ALIAS_PRESENT=1
    fi
    if printf '%s\n' "$output" | awk -v address="$address" \
        '$1 == "inet" && $2 != address { found = 1 } END { exit !found }'
    then
        INSPECTED_ORDINARY_IPV4_PRESENT=1
    fi
}

address_present() {
    inspect_interface_addresses "$1" "$2" || return $?
    [[ "$INSPECTED_ALIAS_PRESENT" = 1 ]]
}
```

Add readiness after a successful address inspection:

```bash
alias_interface_ready() {
    local interface=$1
    local carrier_file="$SYS_CLASS_NET_ROOT/$interface/carrier"
    local carrier

    valid_interface "$interface" || return 2
    if ! IFS= read -r carrier < "$carrier_file"; then
        printf 'failed to inspect Ethernet carrier\n' >&2
        return 2
    fi
    case "$carrier" in
        0) return 1 ;;
        1) ;;
        *)
            printf 'invalid Ethernet carrier state\n' >&2
            return 2
            ;;
    esac
    [[ "$INSPECTED_ORDINARY_IPV4_PRESENT" = 1 ]]
}
```

In `start_alias`, use the inspection performed by `prepare_alias_state`. Add the address only when `alias_interface_ready "$ALIAS_ETH"` returns zero. Return success and retain state for a known-unready or inspection-error result, emit one waiting message, and still apply `arp_ignore=1`.

- [ ] **Step 5: Run focused tests and the full network test file**

Run:

```bash
pytest -q tests/test_zerolab_network_config.py
```

Expected: all tests pass. Existing exact command sequences remain stable because the readiness check uses the same address inspection plus an unlogged sysfs carrier read.

- [ ] **Step 6: Commit the startup gate**

```bash
git add deploy/zerolab-network-config tests/test_zerolab_network_config.py
git commit -m "fix: wait for ordinary network before ZeroLab alias"
```

---

### Task 2: Withdraw and Restore the Alias Across Link Flaps

**Files:**
- Modify: `tests/test_zerolab_network_config.py:1000-1100`
- Modify: `deploy/zerolab-network-config:433-538,540-632`

**Interfaces:**
- Consumes `inspect_interface_addresses ADDRESS INTERFACE` and `alias_interface_ready INTERFACE` from Task 1.
- Produces `reconcile_alias_address ADDRESS INTERFACE ORIGIN`, returning `0` after a successful/no-op cycle and nonzero after an inspection or mutation failure.
- Preserves the saved state directory unchanged during temporary withdrawal and restoration.

- [ ] **Step 1: Write a failing service-owned link-flap test**

```python
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
```

- [ ] **Step 2: Write preexisting ownership and uncertain-inspection tests**

Add tests proving:

```python
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
```

Add a delete-failure retry test: set carrier down, set ordinary addresses empty, set `ip_failure_control` to `del`, verify repeated delete attempts retain the snapshot, clear the failure, and verify withdrawal succeeds.

- [ ] **Step 3: Run new tests and confirm RED**

Run:

```bash
pytest -q \
  tests/test_zerolab_network_config.py::test_alias_run_withdraws_until_ordinary_network_recovers \
  tests/test_zerolab_network_config.py::test_alias_run_never_withdraws_preexisting_alias \
  tests/test_zerolab_network_config.py::test_alias_run_does_not_mutate_address_when_carrier_is_unreadable
```

Expected: the service-owned withdrawal test fails because current reconciliation only adds missing aliases.

- [ ] **Step 4: Implement ownership-aware address reconciliation**

Extract the address branch from `reconcile_alias`:

```bash
reconcile_alias_address() {
    local address=$1
    local ethernet=$2
    local address_origin=$3
    local readiness

    if ! inspect_interface_addresses "$address" "$ethernet"; then
        return 1
    fi
    if alias_interface_ready "$ethernet"; then
        readiness=ready
    else
        case $? in
            1) readiness=unready ;;
            *) return 1 ;;
        esac
    fi

    case "$readiness:$address_origin:$INSPECTED_ALIAS_PRESENT" in
        ready:*:1|unready:preexisting:*|unready:added:0)
            return 0
            ;;
        ready:*:0)
            state_context_is_trusted || return 1
            "$IP_BIN" address add "$address" dev "$ethernet" || return 1
            printf 'ZeroLab alias address restored\n' >&2
            ;;
        unready:added:1)
            state_context_is_trusted || return 1
            "$IP_BIN" address del "$address" dev "$ethernet" || return 1
            printf 'ZeroLab alias withdrawn while ordinary Ethernet is unavailable\n' >&2
            ;;
        *)
            return 1
            ;;
    esac
}
```

Keep the existing state validation at the top of `reconcile_alias`. Record a
nonzero result from `reconcile_alias_address` in `address_failed`, continue to
the independent `arp_ignore` reconciliation, and return nonzero at the end when
either branch failed. This ensures an address-inspection failure does not block
repair of `arp_ignore`. Retain the generic retry message on failure. Do not
rewrite any state file during withdrawal or restoration.

- [ ] **Step 5: Gate missing preexisting-alias restoration during cleanup**

In `cleanup_saved_state`, when the alias is missing and `address_origin=preexisting`, call `alias_interface_ready` using the ordinary-address result from the same inspection. If it is ready, restore as before. If it is unready or unknown, print `preexisting alias restoration deferred until ordinary Ethernet is ready`, set `cleanup_failed=1`, retain the saved state, and do not run `ip address add`.

Add a test that starts from a missing preexisting alias with carrier down, verifies `stop` fails without an add and retains state, restores carrier plus an ordinary address, and verifies a second `stop` restores the alias and removes state.

- [ ] **Step 6: Run all network tests**

```bash
pytest -q tests/test_zerolab_network_config.py
```

Expected: all tests pass, including existing rollback retry and state-trust cases.

- [ ] **Step 7: Commit link-flap reconciliation**

```bash
git add deploy/zerolab-network-config tests/test_zerolab_network_config.py
git commit -m "fix: withdraw ZeroLab alias during link recovery"
```

---

### Task 3: Document the Alias Readiness Contract

**Files:**
- Modify: `deploy/README-zerolab-network.md:115-184`
- Modify: `tests/test_zerolab_network_config.py:20-95`

**Interfaces:**
- Documents the user-visible meanings of active-ready, waiting, withdrawal, recovery, and direct rollback.
- Does not introduce new configuration keys.

- [ ] **Step 1: Add failing documentation assertions**

Extend `test_deployment_guide_covers_modes_safety_and_rollback` with exact required phrases:

```python
for required_text in [
    "ordinary IPv4 address",
    "waiting for ordinary Ethernet network",
    "does not edit NetworkManager profiles",
    "automatically restores the alias",
]:
    assert required_text in text
```

- [ ] **Step 2: Run the documentation test and confirm RED**

```bash
pytest -q tests/test_zerolab_network_config.py::test_deployment_guide_covers_modes_safety_and_rollback
```

Expected: FAIL because the guide does not yet describe the new readiness contract.

- [ ] **Step 3: Update the alias and diagnostics sections**

Add a paragraph before the alias configuration block stating:

```text
Alias mode requires the receiving Ethernet interface to have carrier and an
ordinary IPv4 address supplied by the customer's DHCP or static configuration.
The ZeroLab service does not edit NetworkManager profiles. While that ordinary
network is unavailable, the service remains active and logs "waiting for
ordinary Ethernet network" without adding its /32. After the ordinary network
returns, the supervisor automatically restores the alias within its next
reconciliation cycle.
```

Add verification commands showing both ordinary and alias addresses separately. Explain that loss of the ordinary address causes a service-owned alias to be withdrawn temporarily and does not change its rollback ownership.

- [ ] **Step 4: Run documentation and network tests**

```bash
pytest -q tests/test_zerolab_network_config.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit documentation**

```bash
git add deploy/README-zerolab-network.md tests/test_zerolab_network_config.py
git commit -m "docs: explain ZeroLab alias link recovery"
```

---

### Task 4: Local Regression and Scope Review

**Files:**
- Verify only; no planned source edits.
- Create untracked verification outputs only under `build-alias-link-recovery`,
  `install-alias-link-recovery`, and `log-alias-link-recovery`.

**Interfaces:**
- Consumes the implementation and documentation from Tasks 1-3.
- Produces `install-alias-link-recovery` for staged robot deployment.
- Produces evidence suitable for deciding whether the candidate may be uploaded to ELF3-81.

- [ ] **Step 1: Run syntax, focused tests, and the complete Python suites**

```bash
bash -n deploy/zerolab-network-config
python3 -m py_compile tests/test_zerolab_network_config.py
pytest -q tests/test_zerolab_network_config.py
pytest -q src/bxi_example_py_elf3/mods/com.bxi.sonic/tests
```

Expected: shell/Python syntax succeeds; all network tests pass; SONIC reports 292 passed.

- [ ] **Step 2: Verify systemd packaging and Release builds**

```bash
pytest -q \
  tests/test_zerolab_network_config.py::test_network_unit_uses_optional_config_and_notify_supervisor \
  tests/test_zerolab_network_config.py::test_network_unit_verifies_with_staged_helper

colcon --log-base log-alias-link-recovery build \
  --build-base build-alias-link-recovery \
  --install-base install-alias-link-recovery \
  --merge-install \
  --cmake-args -DCMAKE_BUILD_TYPE=Release \
  --packages-select bxi_example_py_elf3 remote_controller
```

Expected: unit verification has no ZeroLab unit error; both packages finish successfully.

- [ ] **Step 3: Run the C++ A/Y mapping test**

```bash
ctest \
  --test-dir build-alias-link-recovery/remote_controller \
  -R '^remote_controller_control_rules_test$' \
  --output-on-failure
```

Expected: `1/1` passed.

- [ ] **Step 4: Audit the exact branch scope**

```bash
git diff --check sonic-upstream/main...HEAD
git status --short
git diff --name-only sonic-upstream/main...HEAD
git log --oneline --decorate sonic-upstream/main..HEAD
```

Expected: no whitespace error; only the approved network-mode files and design/plan documents differ. Untracked build/install/log directories remain untracked and unstaged.

- [ ] **Step 5: Request code review before robot deployment**

Use the `requesting-code-review` skill. Resolve any High or Medium correctness finding with a failing test before changing implementation. Re-run Steps 1-4 after the final review fix.

---

### Task 5: ELF3-81 Network-Only Acceptance

**Files:**
- Deploy staged copy of: `deploy/zerolab-network-config`
- Do not start either robot controller during this task.

**Interfaces:**
- Requires robot Wi-Fi SSH at `192.168.89.152` and a mechanically safe robot.
- Requires `zerolab-hardware.service` and `ros_elf_launch.service` inactive with no controller processes.
- Produces acceptance evidence for normal DHCP preservation, alias withdrawal/restoration, and exact direct rollback.

- [ ] **Step 1: Upload to a checksum-verifiable temporary path**

From the development host, copy only the helper to the temporary staging path
and compare its checksum. Do not overwrite the installed helper in the upload
step:

```bash
set -Eeuo pipefail

ROBOT=bxi@192.168.89.152
LOCAL_HELPER=$PWD/deploy/zerolab-network-config
REMOTE_DIR=/tmp/zerolab-alias-link-recovery

test -x "$LOCAL_HELPER"
LOCAL_SHA=$(sha256sum "$LOCAL_HELPER" | awk '{print $1}')
ssh "$ROBOT" "mkdir -p '$REMOTE_DIR' && chmod 700 '$REMOTE_DIR'"
scp "$LOCAL_HELPER" "$ROBOT:$REMOTE_DIR/zerolab-network-config"
REMOTE_SHA=$(
  ssh "$ROBOT" \
    "sha256sum '$REMOTE_DIR/zerolab-network-config' | cut -d' ' -f1"
)
test "$REMOTE_SHA" = "$LOCAL_SHA"
printf 'STAGED_HELPER_SHA=%s\n' "$LOCAL_SHA"
```

- [ ] **Step 2: Back up and install with all controllers stopped**

On ELF3-81, verify `ZEROLAB_NETWORK_MODE=direct`, both controller services
inactive, and no matching processes. Then create and print a new root-only
backup, stop only the network service, and install the staged helper:

```bash
set -Eeuo pipefail

grep -Fxq 'ZEROLAB_NETWORK_MODE=direct' /etc/default/zerolab-network
test "$(systemctl is-active zerolab-hardware.service 2>/dev/null || true)" != active
test "$(systemctl is-active ros_elf_launch.service 2>/dev/null || true)" != active
! pgrep -f \
  '[h]ardware_elf3|[b]xi_example_py_elf3_demo|[z]erolab_source|[r]emote_controller'

BACKUP_DIR=$(
  sudo mktemp -d \
    /home/bxi/zerolab-network-backups/alias-link-recovery.XXXXXX
)
sudo chmod 0700 "$BACKUP_DIR"
sudo cp -a \
  /usr/local/libexec/zerolab-network-config \
  "$BACKUP_DIR/zerolab-network-config"
sudo systemctl stop zerolab-network.service
sudo install -m 0755 \
  /tmp/zerolab-alias-link-recovery/zerolab-network-config \
  /usr/local/libexec/zerolab-network-config
bash -n /usr/local/libexec/zerolab-network-config
printf 'BACKUP_DIR=%s\n' "$BACKUP_DIR"
```

- [ ] **Step 3: Establish the ordinary-network baseline**

With the bridge connected and powered, verify:

```text
enp86s0 carrier=1
NetworkManager active UUID=eb0a2ae6-28df-3743-8636-fb601e4cb771
enp86s0 has an ordinary DHCP IPv4 such as 192.168.89.106/16
wlo1 retains 192.168.89.152/16
```

Record the complete IPv4 address set, Wi-Fi `arp_ignore`, active NetworkManager UUID, and `/run/zerolab-network` state.

- [ ] **Step 4: Enable alias and verify steady state**

Write the approved ELF3-81 alias configuration (`192.168.88.213`, `enp86s0`, `wlo1`), restart only `zerolab-network.service`, and verify the exact `/32`, `arp_ignore=1`, active normal NetworkManager UUID, and saved ownership state `address.origin=added`.

- [ ] **Step 5: Power-cycle the bridge and observe withdrawal/recovery**

With both controllers still stopped, power off or disconnect the bridge. Verify the ordinary Ethernet IPv4 disappears and the service-owned `/32` is absent before NetworkManager can create an external `disabled` profile. Restore bridge power. Verify the DHCP profile regains an ordinary IPv4 first, then the `/32` reappears within the next reconciliation interval. Capture NetworkManager and `zerolab-network.service` journals with absolute timestamps.

- [ ] **Step 6: Return to direct and verify exact rollback**

Write only `ZEROLAB_NETWORK_MODE=direct`, restart `zerolab-network.service`, and compare stable address fields with the recorded direct baseline. Verify the alias is absent, Wi-Fi `arp_ignore` equals its original value, the normal DHCP UUID remains active, and `/run/zerolab-network` is absent.

- [ ] **Step 7: Keep strict alias UDP marked separately**

Do not mark strict alias UDP passed with the current bridge. Its observed failure to forward bidirectional unicast remains an external acceptance blocker. Direct-mode UDP and A/Y control results from the earlier run remain valid because this patch does not touch those paths.

- [ ] **Step 8: Leave the robot in the approved final state**

Leave `ZEROLAB_NETWORK_MODE=direct`, `zerolab-network.service=active`, both controller services inactive, no controller processes, ordinary Ethernet DHCP restored, and Wi-Fi SSH intact. Report backup path, installed checksum, journals, and every PASS/FAIL gate before considering push or PR creation.

---

### Task 6: Final Commit/Push Readiness

**Files:**
- Verify only; no planned source edits.

**Interfaces:**
- Requires all local tests and network-only robot acceptance except strict alias UDP to have recorded results.
- Produces a branch ready for a maintainer PR; it does not authorize merge.

- [ ] **Step 1: Re-run verification-before-completion**

Use the `verification-before-completion` skill and repeat Task 4 commands from a clean index. Do not reuse earlier test output after any code change.

- [ ] **Step 2: Confirm all intended changes are committed**

```bash
git status --short
git diff --check sonic-upstream/main...HEAD
git log --oneline sonic-upstream/main..HEAD
```

Expected: only user-owned build/install/log directories are untracked; no intended source or documentation change is unstaged.

- [ ] **Step 3: Present the exact push and PR target for approval**

Report the local head SHA, remote name, target repository, source branch, base branch, scope, test evidence, ELF3-81 acceptance evidence, and the explicit external bridge limitation. Push and create/update the PR only after the user confirms those exact targets.
