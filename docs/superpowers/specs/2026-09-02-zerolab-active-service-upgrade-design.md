# ZeroLab Active-Service Upgrade Design

**Date:** 2026-09-02
**Status:** Approved for implementation planning

## Context

The deployment guide backs up an existing ZeroLab network installation and
then replaces its helper, configuration, unit, and hardware drop-in. Its final
command currently uses `systemctl enable --now zerolab-network.service`.

`enable --now` starts an inactive unit, but it does not restart a unit that is
already active. An upgrade can therefore replace the files on disk while the
old helper process continues running. The installation can appear successful
even though the new network behavior has not taken effect.

ELF3-81 is not affected by this documentation defect: its accepted deployment
explicitly stopped the old network service before replacing the helper and
started the new service afterward.

## Selected Upgrade Sequence

The installation guide will use one lifecycle for fresh installs and upgrades:

1. snapshot the prior enabled and active service state and back up all existing
   files as it does today;
2. if the old network unit exists, stop it before replacing any file so its own
   helper performs cleanup;
3. install the new helper, configuration, unit, and hardware drop-in;
4. verify the unit and run `systemctl daemon-reload`;
5. enable the network unit explicitly;
6. use `systemctl restart zerolab-network.service`, which starts a fresh unit
   on both fresh installs and upgrades;
7. require the new service to report active.

The existence check must allow a fresh installation where the unit is not yet
known to systemd. A failure to stop a known unit remains fatal; the guide must
not hide cleanup failures with an unconditional `|| true`.

## Rejected Alternatives

- Replacing the helper and restarting afterward would execute stop cleanup
  through the newly installed helper rather than the helper that owns the
  running state. Stopping before replacement keeps cleanup paired with its
  original version.
- Keeping `enable --now` and adding another restart would work, but duplicates
  lifecycle operations and obscures which command activates the new process.
- Rebooting the robot would eventually load the new files, but is unnecessary
  and could start enabled robot controller services during deployment.

## Safety and Scope

This change modifies only the deployment guide and its documentation
regression tests. It does not modify the network helper, systemd unit,
hardware dependency, A/Y mappings, ARM and pause behavior, PD Brake, Normal,
the sender allowlist, or UDP port 18000.

Stopping or restarting the network unit must not start either robot controller
service. The existing non-blocking `Wants=` and `After=` relationship remains
unchanged.

## Testing

A new regression assertion will extract the installation shell block and prove
the following ordered contract:

1. the conditional old-unit check and stop occur before the first file install;
2. `daemon-reload` occurs after file installation;
3. explicit enable occurs before explicit restart;
4. the active-state check occurs after restart;
5. `enable --now` is absent from the installation block.

The focused documentation test must fail against the current guide before the
guide is changed, then pass after the minimal documentation correction. Final
verification repeats the complete network suite, focused systemd tests, 292
SONIC tests, two-package Release build, C++ A/Y CTest, diff audit, and
independent code review.
