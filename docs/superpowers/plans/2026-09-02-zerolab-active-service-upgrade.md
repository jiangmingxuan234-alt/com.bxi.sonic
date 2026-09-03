# ZeroLab Active-Service Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a ZeroLab network upgrade stops an existing service before replacing its files and explicitly activates the newly installed helper afterward.

**Architecture:** Keep runtime code and systemd relationships unchanged. Strengthen the deployment guide's installation lifecycle and lock its exact command ordering with a focused documentation regression test.

**Tech Stack:** Markdown shell examples, Python 3, pytest, systemd CLI, ROS 2/colcon, CTest.

## Global Constraints

- Modify only `deploy/README-zerolab-network.md` and `tests/test_zerolab_network_config.py` during implementation.
- Do not change the network helper, systemd units, hardware dependency, A/Y mappings, ARM/pause behavior, PD Brake, Normal, sender allowlist, or UDP port 18000.
- A known old network unit must stop successfully before any installed file is replaced; do not hide stop failures with `|| true`.
- A fresh install where the old unit is unknown to systemd must continue without attempting to stop it.
- Installation must use explicit `enable` followed by explicit `restart`; `enable --now` is forbidden in the installation block.
- Do not stage, delete, or modify existing untracked `build*`, `install*`, or `log*` directories.

---

### Task 1: Lock and Correct the Active-Upgrade Lifecycle

**Files:**
- Modify: `tests/test_zerolab_network_config.py:12-105`
- Modify: `deploy/README-zerolab-network.md:7-82`

**Interfaces:**
- Consumes: the `## Back up and install` fenced shell block in the deployment guide.
- Produces: an ordered installation contract: optional old-unit stop, file installation, unit verification, daemon reload, explicit enable, explicit restart, active-state verification.

- [ ] **Step 1: Write the failing installation-order test**

Add this focused test after `test_packaged_default_is_direct`:

```python
def test_deployment_install_restarts_an_already_active_network_service():
    text = (ROOT / "deploy/README-zerolab-network.md").read_text(
        encoding="utf-8"
    )
    install_block = text.split(
        "## Back up and install", maxsplit=1
    )[1].split("~~~bash", maxsplit=1)[1].split("~~~", maxsplit=1)[0]

    old_unit_check = (
        "if systemctl cat zerolab-network.service >/dev/null 2>&1; then"
    )
    stop = "sudo systemctl stop zerolab-network.service"
    first_install = (
        'sudo install -Dm 0755 "$REPO_ROOT/deploy/zerolab-network-config" '
        "/usr/local/libexec/zerolab-network-config"
    )
    daemon_reload = "sudo systemctl daemon-reload"
    enable = "sudo systemctl enable zerolab-network.service"
    restart = "sudo systemctl restart zerolab-network.service"
    active = "systemctl is-active zerolab-network.service"

    assert "sudo systemctl enable --now zerolab-network.service" not in install_block
    assert install_block.index(old_unit_check) < install_block.index(stop)
    assert install_block.index(stop) < install_block.index(first_install)
    assert install_block.index(first_install) < install_block.index(daemon_reload)
    assert install_block.index(daemon_reload) < install_block.index(enable)
    assert install_block.index(enable) < install_block.index(restart)
    assert install_block.index(restart) < install_block.rindex(active)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_deployment_install_restarts_an_already_active_network_service
```

Expected: FAIL because the guide has no conditional old-unit check or pre-install stop and still contains `enable --now`.

- [ ] **Step 3: Apply the minimal installation-guide correction**

After the four `backup_one` calls and before the first `sudo install`, add:

```bash
if systemctl cat zerolab-network.service >/dev/null 2>&1; then
    sudo systemctl stop zerolab-network.service
fi
```

Replace:

```bash
sudo systemctl enable --now zerolab-network.service
```

with:

```bash
sudo systemctl enable zerolab-network.service
sudo systemctl restart zerolab-network.service
```

Do not change any other deployment commands or runtime files.

- [ ] **Step 4: Run focused and complete network tests and verify GREEN**

Run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_deployment_install_restarts_an_already_active_network_service
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
bash -n deploy/zerolab-network-config
git diff --check
```

Expected: focused test passes; complete network suite reports 66 passed; shell syntax and whitespace checks exit 0.

- [ ] **Step 5: Review and commit the fix**

Verify that only the two planned files changed, then run:

```bash
git add deploy/README-zerolab-network.md tests/test_zerolab_network_config.py
git commit -m "docs: restart ZeroLab service after upgrades"
```

Expected: one implementation commit containing only the guide and regression test.

---

### Task 2: Full Regression, Scope Audit, and Review

**Files:**
- Verify only; create fresh build, install, log, ROS log, and bytecode outputs beneath one new `/tmp/zerolab-active-upgrade-final.XXXXXX` directory.

**Interfaces:**
- Consumes: Task 1's committed deployment sequence and the complete branch overlay.
- Produces: fresh evidence for push/PR readiness and an independent reviewer verdict.

- [ ] **Step 1: Re-run syntax, complete network, and focused systemd tests**

Run from the overlay worktree with `PYTHONPYCACHEPREFIX` pointed beneath the new temporary verification root:

```bash
bash -n deploy/zerolab-network-config
python3 -m py_compile tests/test_zerolab_network_config.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/test_zerolab_network_config.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tests/test_zerolab_network_config.py::test_network_unit_uses_optional_config_and_notify_supervisor \
  tests/test_zerolab_network_config.py::test_network_unit_verifies_with_staged_helper
```

Expected: syntax succeeds, network reports 66 passed, and focused systemd reports 2 passed.

- [ ] **Step 2: Construct a disposable full ROS workspace**

Use `/home/fazepurple/ros2_ws/bxi_rl_controller_ros2_example_dev` as `HOST_WS`, archive `test/konodoki-dev` into the temporary verification root, then archive the current overlay `HEAD` over `src/bxi_example_py_elf3/mods/com.bxi.sonic`. Record and compare the host checkout's porcelain-status SHA-256 before and after both archives.

Expected: host status hashes are identical and no source or existing worktree artifact is modified.

- [ ] **Step 3: Run complete SONIC, Release build, and A/Y CTest**

Source ROS Humble and the existing `install-wireless-auto-recovery/setup.bash`, set the disposable overlay `PYTHONPATH`, and run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
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

Then run:

```bash
colcon --log-base "$VERIFY_ROOT/log-active-upgrade" build \
  --merge-install \
  --base-paths "$VERIFY_ROOT/src" \
  --packages-ignore bxi_depth_camera \
  --packages-select bxi_example_py_elf3 remote_controller \
  --allow-overriding bxi_example_py_elf3 remote_controller \
  --build-base "$VERIFY_ROOT/build-active-upgrade" \
  --install-base "$VERIFY_ROOT/install-active-upgrade" \
  --cmake-args -DCMAKE_BUILD_TYPE=Release

ctest \
  --test-dir "$VERIFY_ROOT/build-active-upgrade/remote_controller" \
  -R '^remote_controller_control_rules_test$' \
  --output-on-failure
```

Expected: 292 passed; two packages finished; CTest passes 1/1.

- [ ] **Step 4: Audit branch scope and host preservation**

Run:

```bash
git diff --check sonic-upstream/main...HEAD
git status --short
git diff --name-only sonic-upstream/main...HEAD
git log --oneline --decorate sonic-upstream/main..HEAD
```

Recompute the host checkout status SHA-256 and require it to equal the pre-archive value. Confirm no tracked working-tree changes and no build/install/log artifact is staged.

- [ ] **Step 5: Request independent final review**

Give the reviewer the three approved specs, this plan, base `sonic-upstream/main`, final `HEAD`, all fresh local results, and the ELF3-81 acceptance evidence. Require Critical/Important/Minor findings and an explicit APPROVE or BLOCK verdict.

Expected: no Critical or Important findings and APPROVE for push/PR. If blocked, return to Task 1 with a new failing regression test for each valid finding.
