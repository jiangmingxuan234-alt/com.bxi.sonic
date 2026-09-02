# ZeroLab Manual Hardware Start Design

## Goal

Preserve the robot's pre-ZeroLab hardware startup behavior: boot must not
automatically start the candidate `hardware_elf3 + bxi_example_py_elf3_demo`
stack. Operators may still start that candidate stack explicitly for a
controlled test.

## Boot behavior

The installed services have independent responsibilities:

- `zerolab-network.service` remains enabled at boot. It prepares the selected
  direct or alias receive-network mode and never starts a hardware controller.
- `ros_elf_launch.service` retains its pre-ZeroLab boot role as the tablet
  remote-controller service. It must not pull in `zerolab-hardware.service`.
- `zerolab-hardware.service` remains installed as a manual test entry point but
  is disabled at boot. Its inactive boot state restores the pre-ZeroLab
  candidate-hardware behavior.

The intended state after boot is:

```text
zerolab-network.service=active
ros_elf_launch.service=active
zerolab-hardware.service=inactive
```

The network service being active does not enter PD Brake, Normal, ZeroLab, or
ARM. In direct mode it also makes no IP-address or `arp_ignore` changes.

## Manual operation

After completing the existing robot-support and emergency-stop safety checks,
an operator may explicitly start the candidate hardware stack with:

```bash
sudo systemctl start zerolab-hardware.service
```

The service is not masked, so this controlled manual path remains available.
Stopping a live controller continues to require the existing safe shutdown
procedure; this design does not add an automatic stop action.

## Deployment and migration

The repository's network deployment enables and restarts only
`zerolab-network.service`. It must not start or enable either hardware
controller service.

Robots that previously installed commit `c3a4f2e` require a one-time migration:
after confirming both controller services and their processes are stopped,
disable `zerolab-hardware.service` without masking it. Leave
`ros_elf_launch.service` and `zerolab-network.service` enabled.

This migration changes only the next-boot enablement state. It must not use
`disable --now` as a shortcut for stopping a live controller.

## Regression protection

Automated deployment tests will assert that the documented installation block:

- enables and restarts `zerolab-network.service`;
- does not `start`, `enable`, or `enable --now`
  `zerolab-hardware.service`;
- does not `start`, `enable`, or `enable --now`
  `ros_elf_launch.service`;
- installs only the non-blocking hardware drop-in, whose dependency direction
  lets a manually started hardware service want the network service but never
  lets the network service pull in hardware.

Documentation will state the expected post-boot states and the explicit manual
hardware-start command. Existing A/Y, ARM, pause, PD Brake, Normal, emergency
stop, sender allowlist, and UDP port 18000 behavior remain unchanged.

## Acceptance criteria

The change is accepted when:

1. Focused tests prove the network installer cannot start or enable a hardware
   controller service.
2. The network unit has no dependency on `zerolab-hardware.service` or
   `ros_elf_launch.service`.
3. The hardware drop-in remains non-blocking and does not provide a reverse
   boot dependency from the network service.
4. The deployment guide clearly distinguishes boot-time network preparation
   from manual candidate-hardware startup.
5. On `elf3-81`, a read-only verification after the one-time disable operation
   reports network and tablet services enabled, candidate hardware disabled,
   and no candidate controller process before manual testing.
