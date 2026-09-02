# ZeroLab Manual Hardware Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the candidate ZeroLab hardware controller disabled at boot while retaining its explicit manual-start entry point and the independently enabled ZeroLab network service.

**Architecture:** The clean branch continues to own only the ZeroLab network supervisor and a one-way, non-blocking hardware drop-in. A documentation-backed regression test prevents the network installer from starting or enabling either hardware controller service, while a guarded one-time robot migration removes the stale `zerolab-hardware.service` enablement left by commit `c3a4f2e`.

**Tech Stack:** systemd unit files, Bash deployment commands, Python 3, pytest

## Global Constraints

- `zerolab-hardware.service` remains installed and manually startable, but disabled at boot.
- `ros_elf_launch.service` retains its pre-ZeroLab tablet remote-controller boot role.
- `zerolab-network.service` remains enabled at boot and must never pull in a hardware controller.
- Do not mask `zerolab-hardware.service`.
- Do not use `disable --now` to stop a live controller.
- Do not change A/Y, ARM, pause, PD Brake, Normal, emergency stop, sender allowlist, or UDP port 18000 behavior.
- Do not add, delete, or commit existing `build*`, `install*`, or `log*` directories.
- Robot commands are supplied one complete block at a time for the user to run; do not operate the robot remotely.

---

### Task 1: Lock manual hardware startup into deployment policy

**Files:**
- Modify: `tests/test_zerolab_network_config.py`
- Modify: `deploy/README-zerolab-network.md`

**Interfaces:**
- Consumes: the first shell block under `## Back up and install`, `deploy/systemd/zerolab-network.service`, and `deploy/systemd/zerolab-hardware.service.d/10-network.conf`.
- Produces: a documented `## Preserve manual hardware startup` section and a regression test that rejects controller start/enable commands in the network installer.

- [ ] **Step 1: Write the failing deployment-policy test**

Add this test near the existing deployment tests in
`tests/test_zerolab_network_config.py`:

```python
def test_deployment_preserves_manual_hardware_startup():
    text = (ROOT / "deploy/README-zerolab-network.md").read_text(
        encoding="utf-8"
    )
    install_block = text.split(
        "## Back up and install", maxsplit=1
    )[1].split("~~~bash", maxsplit=1)[1].split("~~~", maxsplit=1)[0]

    for service in [
        "zerolab-hardware.service",
        "ros_elf_launch.service",
    ]:
        for action in ["start", "enable", "enable --now"]:
            assert f"sudo systemctl {action} {service}" not in install_block

    assert "## Preserve manual hardware startup" in text
    manual_section = text.split(
        "## Preserve manual hardware startup", maxsplit=1
    )[1].split("## Back up and install", maxsplit=1)[0]
    for required_text in [
        "zerolab-network.service=active",
        "ros_elf_launch.service=active",
        "zerolab-hardware.service=inactive",
        "sudo systemctl start zerolab-hardware.service",
        "sudo systemctl disable zerolab-hardware.service",
        "systemctl is-enabled zerolab-hardware.service",
    ]:
        assert required_text in manual_section
    assert "sudo systemctl disable --now zerolab-hardware.service" not in manual_section

    network_unit = (
        ROOT / "deploy/systemd/zerolab-network.service"
    ).read_text(encoding="utf-8")
    assert "zerolab-hardware.service" not in network_unit
    assert "ros_elf_launch.service" not in network_unit
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_deployment_preserves_manual_hardware_startup
```

Expected: FAIL because `## Preserve manual hardware startup` is not yet in the deployment guide. The forbidden start/enable assertions and network-unit dependency assertions should already pass, proving the clean branch does not currently auto-start hardware.

- [ ] **Step 3: Add the minimal deployment documentation**

Insert this section before `## Back up and install` in
`deploy/README-zerolab-network.md`:

````markdown
## Preserve manual hardware startup

This network deployment does not install, start, or enable a hardware
controller. It preserves the pre-ZeroLab startup policy: the tablet remote
controller may start at boot, while the candidate hardware stack starts only
after an operator explicitly requests it.

The intended post-boot state is:

```text
zerolab-network.service=active
ros_elf_launch.service=active
zerolab-hardware.service=inactive
```

Robots that previously installed commit `c3a4f2e` may still have the candidate
hardware service enabled. Perform this one-time migration only while both
controller services and their processes are stopped:

~~~bash
set -Eeuo pipefail

test "$(systemctl is-active zerolab-hardware.service 2>/dev/null || true)" != active
test "$(systemctl is-active ros_elf_launch.service 2>/dev/null || true)" != active
if pgrep -af \
  '[h]ardware_elf3|[b]xi_example_py_elf3_demo|[z]erolab_source|[r]emote_controller'
then
    printf '%s\n' 'controller process is still running; do not change boot state' >&2
    exit 1
fi

sudo systemctl disable zerolab-hardware.service
test "$(systemctl is-enabled zerolab-hardware.service 2>/dev/null || true)" = disabled
test "$(systemctl is-enabled ros_elf_launch.service)" = enabled
test "$(systemctl is-enabled zerolab-network.service)" = enabled
~~~

The migration changes only future boot behavior; it does not use
`disable --now` to stop a live controller and it does not mask the manual
entry point. After the normal robot-support and emergency-stop safety checks,
an operator can still start the candidate stack explicitly:

~~~bash
sudo systemctl start zerolab-hardware.service
~~~
````

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_deployment_preserves_manual_hardware_startup
```

Expected: `1 passed`.

- [ ] **Step 5: Run the deployment-policy neighborhood tests**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_deployment_install_restarts_an_already_active_network_service \
  tests/test_zerolab_network_config.py::test_deployment_guide_covers_modes_safety_and_rollback \
  tests/test_zerolab_network_config.py::test_network_unit_uses_optional_config_and_notify_supervisor \
  tests/test_zerolab_network_config.py::test_hardware_drop_in_keeps_network_non_blocking \
  tests/test_zerolab_network_config.py::test_deployment_preserves_manual_hardware_startup
```

Expected: `5 passed`.

- [ ] **Step 6: Commit the regression policy**

```bash
git add tests/test_zerolab_network_config.py deploy/README-zerolab-network.md
git commit -m "fix: keep ZeroLab hardware startup manual"
```

---

### Task 2: Verify the local branch without changing runtime behavior

**Files:**
- Verify: `deploy/README-zerolab-network.md`
- Verify: `deploy/systemd/zerolab-network.service`
- Verify: `deploy/systemd/zerolab-hardware.service.d/10-network.conf`
- Verify: `tests/test_zerolab_network_config.py`

**Interfaces:**
- Consumes: the committed manual-start policy from Task 1.
- Produces: fresh evidence that the network implementation and repository hygiene remain valid.

- [ ] **Step 1: Run all network tests**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py
```

Expected: every network test passes; the prior baseline was 66 tests, so the new policy test increases the collected count by one.

- [ ] **Step 2: Verify the systemd unit with the staged helper**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_network_unit_verifies_with_staged_helper \
  tests/test_zerolab_network_config.py::test_hardware_drop_in_keeps_network_non_blocking
```

Expected: `2 passed` when `systemd-analyze` and `bwrap` are available; otherwise the staged-helper test may report an explicit environment skip.

- [ ] **Step 3: Verify tracked changes and whitespace**

```bash
git diff --check sonic-upstream/main...HEAD
git status --short --branch
```

Expected: `git diff --check` has no output. Only the known untracked `build*`, `install*`, and `log*` directories may remain; no tracked file is modified after the commit.

---

### Task 3: Remove the stale boot enablement on elf3-81

**Files:**
- Runtime state only: `elf3-81` systemd enablement links

**Interfaces:**
- Consumes: the user-operated robot terminal, the existing safe controller shutdown procedure, and the migration block documented in Task 1.
- Produces: `zerolab-hardware.service=disabled` while `ros_elf_launch.service` and `zerolab-network.service` remain enabled.

- [ ] **Step 1: Run a read-only controller safety gate on the robot**

Give the user one complete terminal block that prints, without changing state:

```bash
set -Eeuo pipefail

for service in \
  zerolab-hardware.service \
  ros_elf_launch.service \
  zerolab-network.service
do
    printf '%s active=%s enabled=%s\n' \
      "$service" \
      "$(systemctl is-active "$service" 2>/dev/null || true)" \
      "$(systemctl is-enabled "$service" 2>/dev/null || true)"
done

pgrep -af \
  '[h]ardware_elf3|[b]xi_example_py_elf3_demo|[z]erolab_source|[r]emote_controller' \
  || printf '%s\n' 'NO_CONTROLLER_PROCESS=PASS'
```

Expected before migration: both controller services are inactive with no matching controller process. If either controller is active, stop and use the existing PD Brake and safe shutdown procedure before continuing.

- [ ] **Step 2: Disable only the candidate hardware boot entry**

After the read-only output passes, give the user this separate complete block:

```bash
set -Eeuo pipefail

test "$(systemctl is-active zerolab-hardware.service 2>/dev/null || true)" != active
test "$(systemctl is-active ros_elf_launch.service 2>/dev/null || true)" != active
if pgrep -af \
  '[h]ardware_elf3|[b]xi_example_py_elf3_demo|[z]erolab_source|[r]emote_controller'
then
    printf '%s\n' 'STOP: controller process is still running' >&2
    exit 1
fi

sudo systemctl disable zerolab-hardware.service

test "$(systemctl is-enabled zerolab-hardware.service 2>/dev/null || true)" = disabled
test "$(systemctl is-enabled ros_elf_launch.service)" = enabled
test "$(systemctl is-enabled zerolab-network.service)" = enabled

printf '%s\n' 'ZEROLAB_HARDWARE_BOOT_DISABLED=PASS'
printf 'zerolab-hardware.service=%s\n' \
  "$(systemctl is-enabled zerolab-hardware.service 2>/dev/null || true)"
printf 'ros_elf_launch.service=%s\n' \
  "$(systemctl is-enabled ros_elf_launch.service 2>/dev/null || true)"
printf 'zerolab-network.service=%s\n' \
  "$(systemctl is-enabled zerolab-network.service 2>/dev/null || true)"
```

Expected: candidate hardware is disabled, tablet and network remain enabled, and no service is started or stopped by this command.

- [ ] **Step 3: Record the acceptance result**

Record the three final `is-enabled` values and `ZEROLAB_HARDWARE_BOOT_DISABLED=PASS` in the handoff. Do not claim reboot validation unless the user separately authorizes and performs a controlled reboot.
