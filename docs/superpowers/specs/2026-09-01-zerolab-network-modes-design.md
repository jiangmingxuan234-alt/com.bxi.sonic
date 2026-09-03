# ZeroLab Direct and Alias Network Modes Design

## Context

The Sonic ZeroLab receiver already binds to `0.0.0.0:18000`, so it can receive
motion-capture UDP through either of two network arrangements:

- **direct**: the sender targets an IPv4 address already assigned to the robot;
- **alias**: a temporary `/32` receive address is added to a selected robot
  interface and the sender targets that address.

The existing ELF3-81 deployment used a fixed alias
`192.168.88.213/32` on `enp86s0` and maintained
`net.ipv4.conf.wlo1.arp_ignore=1`. That robot is a validation platform, not the
source of product defaults. A product deployment must not change customer
networking unless alias mode is explicitly selected, and a ZeroLab network
configuration error must not remove access to PD Brake or Normal.

## Goals

- Use direct mode when no configuration file or mode setting exists.
- Let customers opt into alias mode with a custom IPv4 address.
- Add the configured address as an exact `/32`; customers provide only the
  IPv4 value, not a prefix length.
- Keep alias state repaired once per second while alias mode is active.
- Restore the exact pre-service alias and `arp_ignore` state when alias mode
  stops.
- Clean up state explicitly owned by a prior alias run before direct mode
  becomes ready.
- Keep the network service active and systemd-ready in direct mode without
  adding, deleting, or changing customer network settings.
- Let the hardware controller start even when the optional ZeroLab network
  service fails, so PD Brake and Normal remain available.
- Preserve every existing ZeroLab stream, ARM, pause, dropout, recovery,
  emergency, and sender-filter rule.

## Non-goals

- Do not auto-detect direct versus alias mode.
- Do not auto-discover a robot destination address for the sender.
- Do not change the sender allowlist; it remains `192.168.89.171` in this
  change.
- Do not change UDP port `18000`.
- Do not change A/Y mappings, ROS Domain configuration, hardware launch
  commands, controller installation paths, or PD Brake and Normal behavior.
- Do not put ELF3-81-specific hardware launch paths into the standalone Sonic
  repository.

## Repository and deployment assets

The change will be a new branch and pull request based on the main branch that
already contains the ZeroLab realtime teleoperation merge. The standalone
Sonic repository will own only the network deployment layer:

```text
deploy/zerolab-network-config
deploy/config/zerolab-network
deploy/systemd/zerolab-network.service
deploy/systemd/zerolab-hardware.service.d/10-network.conf
deploy/README-zerolab-network.md
tests/test_zerolab_network_config.py
```

The hardware file is a systemd drop-in, not a replacement hardware unit. It
adds an optional startup relationship to the already-installed
`zerolab-hardware.service` without changing that service's command, user,
environment, restart policy, or paths.

## Configuration contract

The service reads an optional file:

```text
/etc/default/zerolab-network
```

The packaged default example contains:

```bash
ZEROLAB_NETWORK_MODE=direct
```

If the file is absent or `ZEROLAB_NETWORK_MODE` is unset or empty, the helper
uses `direct`. Accepted mode values are exactly lowercase `direct` and
`alias`.

Alias mode requires all of these values:

```bash
ZEROLAB_NETWORK_MODE=alias
ZEROLAB_ALIAS_IP=192.168.88.213
ZEROLAB_ETH=enp86s0
ZEROLAB_WIFI=wlo1
```

`ZEROLAB_ALIAS_IP` may be any valid customer-selected IPv4 address. It must not
contain a CIDR suffix; the helper validates four decimal octets in the range
0–255 and constructs `<ZEROLAB_ALIAS_IP>/32`. Interface values are used only in
alias mode and must pass a conservative Linux interface-name validation before
they are interpolated into commands or sysctl keys.

Changing the file while the service is active takes effect only after an
explicit `systemctl restart zerolab-network.service`.

## Direct mode lifecycle

On startup, the helper first attempts an idempotent rollback of any complete,
validated runtime state left by a previous alias run. This rollback uses the
address, Ethernet interface, ARP sysctl key, ownership marker, and original
sysctl value recorded by that run; it does not infer ownership from the
current network configuration.

After owned-state cleanup succeeds, direct mode:

1. does not add or delete any address;
2. does not read or write `arp_ignore`;
3. notifies systemd that the network service is ready;
4. remains active until stopped;
5. performs no network operation during normal stop.

A fresh direct startup with no owned alias state therefore makes no `ip` or
`sysctl` call. Cleaning a recorded prior alias state is the only permitted
direct-start network mutation.

The sender must target a currently assigned robot IPv4 address on UDP port
`18000`. If DHCP later changes that address, the sender configuration must be
updated; direct mode does not create a stable destination alias.

## Alias mode lifecycle

After cleaning any owned stale state, alias mode validates its mode-specific
configuration and performs a transactional start:

1. record the exact configured `<IP>/32`, Ethernet interface, ARP sysctl key,
   original numeric `arp_ignore`, and whether the alias already existed;
2. add `<ZEROLAB_ALIAS_IP>/32` only when it is absent;
3. set `net.ipv4.conf.<ZEROLAB_WIFI>.arp_ignore=1`;
4. mark the runtime transaction active;
5. notify systemd that initial configuration is ready;
6. reconcile the address and sysctl value once per second.

Reconciliation emits logs only when it repairs drift or an operation fails.
Transient failures retain the original rollback snapshot and retry on the next
cycle.

On stop or normal signal-driven exit, the helper restores the exact observed
baseline:

- an address added by the service is removed if present;
- an address that existed before startup is preserved or restored;
- an already-missing service-added address is a successful idempotent cleanup;
- the original numeric `arp_ignore` value is restored;
- state entries are removed only after their corresponding rollback succeeds.

Changing alias IP or interfaces requires a restart. The old process normally
rolls back its in-memory configuration before the new process starts. If it
previously exited abnormally, the new process uses stored values to clean the
old address and sysctl before applying the new configuration.

## Runtime state and validation

Runtime ownership state lives under `/run/zerolab-network` with root-only
permissions. Separate state entries record the schema version, mode, exact
address, Ethernet interface, ARP sysctl key, original sysctl value, address
ownership, and transaction-active marker.

Every stored value is validated before it can be used in an `ip` or `sysctl`
command. Invalid or incomplete saved state is never guessed at or silently
discarded; cleanup fails with a precise log so an operator can inspect it.
Customer addresses that are not proven to be service-owned are never deleted.

Before applying a newly requested mode, the helper cleans any valid prior
owned state. It then validates the requested mode and all mode-specific
values. Errors include the offending setting and state that the only supported
modes are `direct` and `alias`.

## systemd behavior and PD/Normal availability

`zerolab-network.service` remains a long-running `Type=notify` unit. It reads
the optional environment file, restarts on unexpected runtime failure, and is
ready only after direct cleanup or the initial alias transaction succeeds.

The hardware drop-in uses:

```ini
[Unit]
Wants=zerolab-network.service
After=zerolab-network.service
```

It deliberately does not use `Requires=`. A malformed alias configuration or
network-service failure is visible in systemd and journal logs but does not
block the base hardware controller. Consequently PD Brake and Normal remain
available whenever the existing ROS/tablet control path is otherwise healthy.
This statement concerns only the optional ZeroLab receive configuration; loss
of the robot's underlying ROS communication network can still prevent tablet
commands from arriving.

## ZeroLab stream and ARM safety behavior

Direct and alias modes affect only how UDP reaches the receiver. Both modes use
the existing source gate unchanged:

```text
no acceptable UDP -> WAIT_STREAM
fresh real frames satisfy the existing gate -> WAIT_ARM
explicit Y/btn_10=12 in WAIT_ARM -> two-second blend -> ARMED
```

Pressing A may enter the ZeroLab state without input, but the state remains in
`WAIT_STREAM`. Y is rejected until fresh acceptable data advances it to
`WAIT_ARM`. Packets from a sender other than the unchanged
`192.168.89.171` allowlisted address do not satisfy the gate.

After the system is already armed, existing dropout behavior is unchanged:
short gaps use buffered playout/hold; a stale gap enters `HOLD_REFERENCE` while
Sonic balance continues; ten new real frames trigger the existing smooth
automatic recovery. Y still pauses human takeover, and PD Brake remains the
emergency exit.

## Error behavior

- Invalid mode: network service fails before readiness and logs that only
  `direct` and `alias` are accepted.
- Missing or invalid alias IP/interface: network service fails before applying
  a new alias and logs the exact setting.
- Incomplete owned-state rollback: network service fails readiness and retains
  retryable state.
- Alias application failure: the helper rolls back every successfully applied
  step, exits nonzero, and retains evidence only for operations that still need
  cleanup.
- Reconciliation failure after readiness: the supervisor logs the failure and
  retries without replacing the baseline snapshot.
- Any network-service failure leaves the hardware service free to start; the
  unchanged ZeroLab input gate prevents takeover without fresh data.

## Automated verification

Focused helper and unit tests will cover at least:

- missing configuration defaults to direct;
- explicit direct performs no `ip` or `sysctl` call on fresh start or stop;
- direct cleans valid service-owned alias state left by an earlier run;
- direct never removes an unowned customer address;
- alias accepts a custom IPv4 address and adds exactly `<IP>/32`;
- invalid IPv4, CIDR input, missing interface, and invalid mode fail clearly;
- alias repairs a missing address and changed `arp_ignore` within two cycles;
- alias stop removes a service-added address and restores original sysctl;
- alias stop preserves or restores a pre-existing address;
- changing alias IP cleans the stored old address before adding the new one;
- rollback failures remain retryable;
- direct and alias both send systemd readiness only after their startup gates;
- the hardware drop-in contains `Wants=` and `After=` but not `Requires=`;
- the unit reads the optional `/etc/default/zerolab-network` file;
- shell syntax, unit syntax, and repository diff checks pass.

Integration verification will also rerun the complete Sonic/ZeroLab Python
suite, the network service suite, the Release host build, and the focused C++
A/Y mapping tests. The network-mode change does not claim to add A/Y code; that
regression only proves the deployment did not change existing mappings.

## ELF3-81 validation

ELF3-81 is used only as a validation platform. Tests begin with the robot
reliably supported, a physical emergency stop available, and a safety observer
present.

1. Install the new helper, unit, optional default file, and hardware drop-in.
2. Validate missing-config direct mode: no alias appears and `arp_ignore` is
   unchanged.
3. Point the sender at the robot's current Ethernet IP and verify strict UDP,
   `WAIT_STREAM -> WAIT_ARM`, explicit ARM, pause, and PD Brake/Normal.
4. Configure alias mode with the test bridge IP and restart the network unit.
5. Verify custom `/32` application, `arp_ignore=1`, strict UDP, one-second
   self-repair, and unchanged control behavior.
6. Switch back to direct and verify the service-owned alias and sysctl changes
   are rolled back while PD Brake and Normal remain available.
7. Leave the validation robot in direct mode.

No ARM command is sent until fresh acceptable input reaches `WAIT_ARM` and all
existing hardware safety prerequisites are satisfied.

## Rollback

Stopping alias mode transactionally restores its baseline. The deployment
documentation backs up any installed unit, configuration, and drop-in before
replacement. Repository rollback is a revert of the network-mode pull request;
it does not require reverting the already-merged ZeroLab realtime
teleoperation change.

## Acceptance criteria

- A new installation with no configuration performs no network mutation.
- A customer can enable alias mode with any valid explicitly configured IPv4
  address without editing the helper or unit.
- Direct-to-alias, alias-to-direct, and alias-IP-change restarts leave no
  service-owned stale address or sysctl value.
- Invalid ZeroLab network configuration is obvious in logs but does not remove
  PD Brake or Normal availability.
- Neither direct nor alias can bypass `WAIT_STREAM`, `WAIT_ARM`, or the explicit
  ARM gate.
- Existing sender filtering, ARM/pause, dropout recovery, A/Y mapping, ROS
  Domain, emergency behavior, and hardware launch commands remain unchanged.
