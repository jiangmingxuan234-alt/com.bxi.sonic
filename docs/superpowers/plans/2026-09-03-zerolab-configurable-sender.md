# ZeroLab Configurable Sender Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let customers configure the allowed ZeroLab sender IPv4 without rebuilding the Mod, while preserving `192.168.89.171` as the default and leaving PICO behavior unchanged.

**Architecture:** Add a standard-library-only resolver that applies an explicit `ZEROLAB_ALLOWED_SENDER` environment override to the manifest sender and validates it fail-closed. Load `/etc/default/zerolab-network` into the manually started hardware service, keep direct/alias handling independent, and document that a stopped/restarted hardware process is required for changes to take effect.

**Tech Stack:** Python 3.10, `ipaddress`, pytest, systemd drop-ins, Bash-compatible `/etc/default` configuration, YAML manifest verification.

## Global Constraints

- Keep `192.168.89.171` as the packaged and manifest default.
- Only the exact lowercase keyword `ZEROLAB_ALLOWED_SENDER=any` disables source-IP filtering.
- Reject empty values, IPv6, hostnames, malformed values, and surrounding whitespace; never fall back from invalid input to allow-all.
- Do not change UDP destination port `18000` or add source-port authentication.
- Do not modify `pico_manager`, `smpl_bridge`, `sonic_teleop`, existing PICO events, routes, actions, or ports.
- Do not start, stop, enable, or disable robot controller services during local implementation or verification.
- Preserve manual hardware startup: `zerolab-hardware.service` remains disabled until explicitly started by an operator.
- Preserve all existing untracked build, install, and log directories in the worktree.

## Approved Safety Amendment

Task 4 exposed that systemd `EnvironmentFile` strips unquoted whitespace, so
the initial empty allow-all sentinel could turn an accidental whitespace-only
value into allow-all. The user approved replacing that sentinel with the exact
lowercase keyword `any`. Tasks 1-3 below record the initial TDD sequence; Task 5
supersedes their empty-value behavior, after which every Task 4 verification
step must be rerun.

## File Structure

- Create `zerolab/sender_config.py`: pure environment/manifest precedence and IPv4 validation.
- Create `tests/test_zerolab_sender_config.py`: dependency-light unit tests for the resolver.
- Modify `zerolab/source_node.py`: resolve the effective sender before constructing `ZeroLabUdpReceiver`.
- Modify `deploy/config/zerolab-network`: package the current sender as the default.
- Modify `deploy/systemd/zerolab-hardware.service.d/10-network.conf`: load the optional shared environment file into the manual hardware service.
- Modify `tests/test_zerolab_network_config.py`: protect the packaged default, drop-in, documentation, and manual-start contract.
- Modify `deploy/README-zerolab-network.md`: document customer configuration, process-scoped reload behavior, foreground override, and safe mode switching without losing the sender value.

---

### Task 1: Resolve and validate the effective sender

**Files:**
- Create: `tests/test_zerolab_sender_config.py`
- Create: `zerolab/sender_config.py`

**Interfaces:**
- Consumes: manifest `allowed_sender` as `object`; process environment as `Mapping[str, str]`.
- Produces: `ALLOWED_SENDER_ENV = "ZEROLAB_ALLOWED_SENDER"` and `resolve_allowed_sender(manifest_sender: object, environ: Mapping[str, str]) -> str | None`.

- [ ] **Step 1: Write failing precedence, opt-out, and validation tests**

```python
import pytest

from zerolab.sender_config import resolve_allowed_sender


def test_absent_override_uses_manifest_sender():
    assert resolve_allowed_sender("192.168.89.171", {}) == "192.168.89.171"


def test_environment_sender_overrides_manifest():
    assert resolve_allowed_sender(
        "192.168.89.171",
        {"ZEROLAB_ALLOWED_SENDER": "192.168.89.200"},
    ) == "192.168.89.200"


def test_explicit_empty_environment_sender_disables_filter():
    assert resolve_allowed_sender(
        "192.168.89.171",
        {"ZEROLAB_ALLOWED_SENDER": ""},
    ) is None


@pytest.mark.parametrize(
    "value",
    [
        "customer-pc",
        "2001:db8::1",
        "999.1.1.1",
        " 192.168.89.200",
        "192.168.89.200 ",
    ],
)
def test_invalid_environment_sender_is_rejected(value):
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender(
            "192.168.89.171",
            {"ZEROLAB_ALLOWED_SENDER": value},
        )


def test_invalid_manifest_sender_is_rejected_when_override_is_absent():
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender("customer-pc", {})


def test_non_string_manifest_sender_is_rejected():
    with pytest.raises(ValueError, match="allowed sender"):
        resolve_allowed_sender(171, {})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_sender_config.py
```

Expected: collection fails with `ModuleNotFoundError: No module named 'zerolab.sender_config'`.

- [ ] **Step 3: Implement the minimal resolver**

Create `zerolab/sender_config.py`:

```python
"""Resolve the ZeroLab UDP sender allowlist without ROS dependencies."""

from collections.abc import Mapping
from ipaddress import IPv4Address


ALLOWED_SENDER_ENV = "ZEROLAB_ALLOWED_SENDER"


def resolve_allowed_sender(
    manifest_sender: object,
    environ: Mapping[str, str],
) -> str | None:
    candidate = environ.get(ALLOWED_SENDER_ENV, manifest_sender)
    if not isinstance(candidate, str):
        raise ValueError("allowed sender must be an IPv4 address or empty")
    if candidate == "":
        return None
    if candidate != candidate.strip():
        raise ValueError("allowed sender must not contain surrounding whitespace")
    try:
        address = IPv4Address(candidate)
    except ValueError as error:
        raise ValueError(
            "allowed sender must be an IPv4 address or empty"
        ) from error
    return str(address)
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command.

Expected: `10 passed`.

- [ ] **Step 5: Commit the resolver**

```bash
git add zerolab/sender_config.py tests/test_zerolab_sender_config.py
git commit -m "feat: resolve configurable ZeroLab sender"
```

---

### Task 2: Apply the sender override to the ZeroLab source

**Files:**
- Modify: `zerolab/source_node.py:3-29,659-694`
- Test temporarily: `/tmp/test_zerolab_source_sender_integration.py`

**Interfaces:**
- Consumes: `resolve_allowed_sender(manifest_sender: object, environ: Mapping[str, str]) -> str | None` from Task 1.
- Produces: `ZeroLabSourceNode` passes the resolved `str | None` to `ZeroLabUdpReceiver(allowed_sender_host=...)` before that receiver opens its UDP socket.

- [ ] **Step 1: Add a failing real source/UDP integration test outside the dirty parent checkout**

Create `/tmp/test_zerolab_source_sender_integration.py`. Run it with the Mod
worktree first on `PYTHONPATH`, followed by the parent workspace's framework
source. The test constructs the real ROS node, real UDP receiver, and real ZMQ
publisher; it does not start robot hardware:

```python
from pathlib import Path
import socket
import time

import pytest
import rclpy

from bxi_example_py_elf3.framework.mod_api import NodeBuildContext
from zerolab.source_node import ZeroLabSourceNode


MOD_ROOT = Path(__file__).resolve().parents[1]


def free_port(socket_type):
    probe = socket.socket(socket.AF_INET, socket_type)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def source_context(udp_port, pose_port):
    return NodeBuildContext(
        mod_id="com.bxi.sonic",
        node_id="com.bxi.sonic/zerolab_sender_test",
        node_name="zerolab_sender_test",
        mod_root=MOD_ROOT,
        params={
            "udp_bind_host": "127.0.0.1",
            "udp_port": udp_port,
            "allowed_sender": "127.0.0.1",
            "pose_host": "127.0.0.1",
            "pose_port": pose_port,
            "pose_topic": "pose",
            "rate_hz": 50.0,
            "window_frames": 10,
            "stale_seconds": 0.5,
            "jitter_buffer_seconds": 0.04,
            "short_recovery_blend_seconds": 0.2,
            "recovery_real_frames": 10,
            "record_path": "",
        },
    )


@pytest.fixture
def rclpy_runtime():
    rclpy.init(args=[])
    yield
    if rclpy.ok():
        rclpy.shutdown()


def test_environment_override_rejects_manifest_sender(
    monkeypatch, rclpy_runtime
):
    monkeypatch.setenv("ZEROLAB_ALLOWED_SENDER", "127.0.0.2")
    udp_port = free_port(socket.SOCK_DGRAM)
    node = ZeroLabSourceNode(
        source_context(udp_port, free_port(socket.SOCK_STREAM))
    )
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.bind(("127.0.0.1", 0))
        sender.sendto(bytes(992), ("127.0.0.1", udp_port))
        for _ in range(100):
            node._receiver.poll()
            if node._receiver.stats.received:
                break
            time.sleep(0.001)
        assert node._receiver.stats.unexpected_sender == 1
        assert node._receiver.stats.accepted == 0
    finally:
        sender.close()
        node.destroy_node()


def test_invalid_override_does_not_open_udp_socket(
    monkeypatch, rclpy_runtime
):
    monkeypatch.setenv("ZEROLAB_ALLOWED_SENDER", "customer-pc")
    udp_port = free_port(socket.SOCK_DGRAM)
    with pytest.raises(ValueError, match="allowed sender"):
        ZeroLabSourceNode(
            source_context(udp_port, free_port(socket.SOCK_STREAM))
        )
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", udp_port))
    finally:
        probe.close()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH="$PWD:/home/fazepurple/ros2_ws/bxi_rl_controller_ros2_example_dev/src/bxi_example_py_elf3" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  /tmp/test_zerolab_source_sender_integration.py
```

Expected: the first test fails because the manifest sender is still accepted,
and the second test fails because the hostname is not rejected.

- [ ] **Step 3: Wire the resolver before receiver construction**

In `zerolab/source_node.py`:

```python
import os
```

Add the relative import alongside the existing ZeroLab imports:

```python
from .sender_config import resolve_allowed_sender
```

Resolve immediately after parameter validation and before the ROS node or UDP
receiver is constructed:

```python
params = validate_source_params(
    context.params, mod_root=context.mod_root
)
allowed_sender_host = resolve_allowed_sender(
    params["allowed_sender"], os.environ
)
super().__init__(
    context.node_name, namespace=context.namespace or None
)

# Later, inside the existing guarded resource construction:
self._receiver = ZeroLabUdpReceiver(
    bind_host=str(params["udp_bind_host"]),
    port=int(params["udp_port"]),
    allowed_sender_host=allowed_sender_host,
)
```

Do not catch the resolver's `ValueError`; initialization must stop before the receiver opens its socket.

- [ ] **Step 4: Run resolver and syntax tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_sender_config.py
PYTHONPATH="$PWD:/home/fazepurple/ros2_ws/bxi_rl_controller_ros2_example_dev/src/bxi_example_py_elf3" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  /tmp/test_zerolab_source_sender_integration.py
python3 -m py_compile zerolab/sender_config.py zerolab/source_node.py
```

Expected: all sender unit and integration tests pass and both modules compile.

- [ ] **Step 5: Commit source integration**

```bash
git add zerolab/source_node.py tests/test_zerolab_sender_config.py
git commit -m "feat: apply ZeroLab sender environment override"
```

---

### Task 3: Package and document the customer setting

**Files:**
- Modify: `tests/test_zerolab_network_config.py:16-18,51-85,88-108`
- Modify: `deploy/config/zerolab-network:1`
- Modify: `deploy/systemd/zerolab-hardware.service.d/10-network.conf:1-3`
- Modify: `deploy/README-zerolab-network.md:1-250`

**Interfaces:**
- Consumes: `ZEROLAB_ALLOWED_SENDER` defined in Task 1.
- Produces: packaged default `ZEROLAB_ALLOWED_SENDER=192.168.89.171`; optional systemd environment-file loading for the manually started hardware service; documented customer and foreground workflows.

- [ ] **Step 1: Write failing deployment-contract tests**

Replace `test_packaged_default_is_direct` with:

```python
def test_packaged_defaults_are_direct_and_use_current_sender():
    lines = (ROOT / "deploy/config/zerolab-network").read_text().splitlines()
    assert lines == [
        "ZEROLAB_NETWORK_MODE=direct",
        "ZEROLAB_ALLOWED_SENDER=192.168.89.171",
    ]
```

Add:

```python
def test_manual_hardware_service_loads_optional_sender_configuration():
    text = (
        ROOT / "deploy/systemd/zerolab-hardware.service.d/10-network.conf"
    ).read_text()
    assert "Wants=zerolab-network.service" in text
    assert "After=zerolab-network.service" in text
    assert "[Service]" in text
    assert "EnvironmentFile=-/etc/default/zerolab-network" in text


```

Extend `test_deployment_preserves_manual_hardware_startup` to assert the added
`[Service]` section does not add `WantedBy`, `RequiredBy`, `PartOf`, or
`BindsTo`, and that the installation block still contains no start/enable
operation for `zerolab-hardware.service`.

- [ ] **Step 2: Run deployment tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_network_config.py
```

Expected: FAIL because the packaged sender and hardware `EnvironmentFile` are
absent.

- [ ] **Step 3: Add the packaged default and environment-file loading**

Set `deploy/config/zerolab-network` exactly to:

```bash
ZEROLAB_NETWORK_MODE=direct
ZEROLAB_ALLOWED_SENDER=192.168.89.171
```

Set the drop-in sections to:

```ini
[Unit]
Wants=zerolab-network.service
After=zerolab-network.service

[Service]
EnvironmentFile=-/etc/default/zerolab-network
```

Do not add an `[Install]` section or any reverse dependency.

- [ ] **Step 4: Document configuration and safe reload semantics**

Add a `## Sender allowlist` section explaining these exact cases:

```bash
# Packaged/default sender
ZEROLAB_ALLOWED_SENDER=192.168.89.171

# Customer sender
ZEROLAB_ALLOWED_SENDER=192.168.89.200

# Explicitly accept any source IP
ZEROLAB_ALLOWED_SENDER=
```

State that the value is a source-IP filter only, does not authenticate source
ports or identity, and applies equally in direct and alias modes. State that a
running hardware stack retains its start-time environment: after the existing
PD Brake/mechanical-support/controller-stop procedure, use
`sudo systemctl start zerolab-hardware.service` to start it again; restarting
only `zerolab-network.service` does not apply the sender to a running hardware
process.

For foreground use, document:

```bash
export ZEROLAB_ALLOWED_SENDER=192.168.89.200
ros2 launch bxi_example_py_elf3 example_demo_hw.launch.py
```

Keep `Ctrl+C` as the foreground stop mechanism and state that foreground and
systemd hardware stacks must not run together.

Update every alias/direct configuration heredoc so it includes an explicit
`ZEROLAB_ALLOWED_SENDER` line and cannot accidentally discard a customer's
sender while switching network modes.

- [ ] **Step 5: Run deployment and static validation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_network_config.py
bash -n deploy/zerolab-network-config
systemd-analyze verify deploy/systemd/zerolab-network.service
```

Expected: all deployment tests pass; Bash syntax and the network unit verify
successfully. Any unrelated host-system unit warning is recorded separately and
must not be presented as a ZeroLab PASS.

- [ ] **Step 6: Commit deployment support**

```bash
git add \
  deploy/config/zerolab-network \
  deploy/systemd/zerolab-hardware.service.d/10-network.conf \
  deploy/README-zerolab-network.md \
  tests/test_zerolab_network_config.py
git commit -m "feat: configure ZeroLab sender at deployment"
```

---

### Task 4: Run complete regression and PICO-preservation checks

**Files:**
- Verify: `mod.yaml`
- Verify: `zerolab/sender_config.py`
- Verify: `zerolab/source_node.py`
- Verify: `deploy/config/zerolab-network`
- Verify: `deploy/systemd/zerolab-hardware.service.d/10-network.conf`
- Verify: `deploy/README-zerolab-network.md`
- Verify: `tests/test_zerolab_sender_config.py`
- Verify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Consumes: all deliverables from Tasks 1-3.
- Produces: local evidence only; no robot or service state changes.

- [ ] **Step 1: Run all tracked split-repository tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider tests
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Verify syntax and whitespace**

```bash
python3 -m py_compile zerolab/sender_config.py zerolab/source_node.py
bash -n deploy/zerolab-network-config
git diff --check 9ad60db..HEAD
```

Expected: all commands exit `0`.

- [ ] **Step 3: Prove this feature did not edit the manifest**

```bash
test -z "$(git diff 9ad60db..HEAD -- mod.yaml)"
```

Expected: exit `0`; the feature adds no `mod.yaml` change, so the previously
verified PICO manifest sections remain byte-for-byte unchanged by this work.

- [ ] **Step 4: Run the full parent-workspace ZeroLab/PICO regression in an isolated temporary snapshot**

Create a temporary detached snapshot of parent commit
`c3a4f2ef8e4d9af952ce02d2d29ff9cdf56b0f71`, replace only
`src/bxi_example_py_elf3/mods/com.bxi.sonic` in that snapshot with the tracked
files from this completed Mod commit, and run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  src/bxi_example_py_elf3/test/test_sonic_ordered_playout.py \
  src/bxi_example_py_elf3/test/test_sonic_python_runtime.py \
  src/bxi_example_py_elf3/test/test_sonic_reference_gate.py \
  src/bxi_example_py_elf3/test/test_zerolab_arming.py \
  src/bxi_example_py_elf3/test/test_zerolab_converter.py \
  src/bxi_example_py_elf3/test/test_zerolab_lifecycle.py \
  src/bxi_example_py_elf3/test/test_zerolab_manifest.py \
  src/bxi_example_py_elf3/test/test_zerolab_pose_contract.py \
  src/bxi_example_py_elf3/test/test_zerolab_protocol.py \
  src/bxi_example_py_elf3/test/test_zerolab_recording.py \
  src/bxi_example_py_elf3/test/test_zerolab_resampler.py \
  src/bxi_example_py_elf3/test/test_zerolab_timeline.py \
  src/bxi_example_py_elf3/test/test_zerolab_udp_receiver.py
```

Expected: all selected tests pass. The snapshot stays under `/tmp`, the dirty
parent checkout is not modified, and its existing worktrees/build/install/log
directories are not removed.

- [ ] **Step 5: Review the final tracked diff and service invariants**

```bash
git status --short --untracked-files=no
git diff --stat 9ad60db..HEAD
git diff 9ad60db..HEAD -- \
  zerolab/sender_config.py \
  zerolab/source_node.py \
  deploy/config/zerolab-network \
  deploy/systemd/zerolab-hardware.service.d/10-network.conf \
  deploy/README-zerolab-network.md \
  tests/test_zerolab_sender_config.py \
  tests/test_zerolab_network_config.py
```

Confirm the tracked tree is clean after commits, no existing untracked artifact
was staged, the hardware drop-in contains no boot-enablement directive, and no
test or documentation command starts robot hardware.

- [ ] **Step 6: Report local versus hardware acceptance separately**

Report automated checks as PASS/FAIL with their command output. Keep custom
sender capture, wrong-sender rejection, strict alias UDP, reboot/manual-start,
and complete PICO headset/body-tracking/calibration/live-pose/head/wrist/
gripper/re-entry checks marked `NOT RUN` until their separate guarded robot
acceptance produces evidence.

---

### Task 5: Replace the unsafe empty opt-out with an explicit `any` sentinel

**Files:**
- Modify: `tests/test_zerolab_sender_config.py`
- Modify: `zerolab/sender_config.py`
- Modify: `tests/test_zerolab_network_config.py`
- Modify: `deploy/README-zerolab-network.md`

**Interfaces:**
- Consumes: `resolve_allowed_sender(manifest_sender: object, environ: Mapping[str, str]) -> str | None` and the deployed `ZEROLAB_ALLOWED_SENDER` environment variable.
- Produces: `None` only for the exact lowercase value `any`; empty and whitespace-only values raise `ValueError` before ROS or UDP construction.

- [ ] **Step 1: Write the failing sentinel and fail-closed tests**

Replace the empty opt-out test with:

```python
def test_explicit_any_environment_sender_disables_filter():
    assert resolve_allowed_sender(
        "192.168.89.171",
        {"ZEROLAB_ALLOWED_SENDER": "any"},
    ) is None
```

Add `""`, `" "`, `"ANY"`, `" any"`, and `"any "` to the invalid environment
parameterization. These cases name the production break: returning `None` for
anything other than exact lowercase `any` would unintentionally weaken the
allowlist.

Update the deployment execution tests so reinstall and direct/alias rewrites
preserve both a custom IPv4 and the literal `ZEROLAB_ALLOWED_SENDER=any`; remove
the old expectation that an empty assignment is valid.

- [ ] **Step 2: Run both focused suites and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_sender_config.py \
  tests/test_zerolab_network_config.py
```

Expected: sender tests fail because `any` is rejected and empty returns `None`;
deployment behavior tests fail where the documentation still writes or expects
an empty opt-out.

- [ ] **Step 3: Implement the exact sentinel**

In `zerolab/sender_config.py`, replace the empty branch with:

```python
if candidate == "any":
    return None
```

Leave all other candidates on the exact-whitespace and `IPv4Address` validation
path. Change both error messages to:

```text
allowed sender must be an IPv4 address or 'any'
```

Do not accept case variants, empty strings, whitespace-only strings, hostnames,
or IPv6.

- [ ] **Step 4: Update operator documentation and preservation behavior**

Replace every statement and command that treats an empty sender as allow-all
with the exact lowercase keyword:

```bash
ZEROLAB_ALLOWED_SENDER=any
```

State explicitly that empty and whitespace-only values are invalid and fail
closed. Preserve `any` byte-for-byte during reinstall and direct/alias mode
rewrites in the same way as a customer IPv4.

- [ ] **Step 5: Run focused unit, real integration, and deployment validation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_zerolab_sender_config.py \
  tests/test_zerolab_network_config.py
PYTHONPATH="$PWD:/home/fazepurple/ros2_ws/bxi_rl_controller_ros2_example_dev/src/bxi_example_py_elf3" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python3 -m pytest -q -p no:cacheprovider \
  /tmp/test_zerolab_source_sender_integration.py
python3 -m py_compile zerolab/sender_config.py zerolab/source_node.py
bash -n deploy/zerolab-network-config
git diff --check
```

The integration test's invalid configuration case must include an empty
environment value and prove the UDP port remains unbound. Expected: all commands
exit `0` with no warnings attributable to the changed code.

- [ ] **Step 6: Commit the fail-closed sentinel**

```bash
git add \
  zerolab/sender_config.py \
  tests/test_zerolab_sender_config.py \
  tests/test_zerolab_network_config.py \
  deploy/README-zerolab-network.md
git commit -m "fix: require explicit ZeroLab allow-any sentinel"
```

- [ ] **Step 7: Repeat Task 4 from Step 1**

Rerun all split-repository and isolated parent-workspace checks against the new
HEAD. Complete acceptance remains FAIL if systemd normalization can still turn
an invalid value into allow-all, if `mod.yaml` changed, or if any PICO/ZeroLab
regression fails.
