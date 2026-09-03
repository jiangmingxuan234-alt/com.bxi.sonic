# ZeroLab Alias Link-Recovery Design

**Date:** 2026-09-02
**Status:** Approved for implementation planning

## Context

ZeroLab supports two receive-network modes:

- `direct` leaves host addressing and `arp_ignore` unchanged. The sender targets
  an existing robot address on UDP port 18000.
- `alias` adds a configured `/32` address to a configured Ethernet interface,
  sets `arp_ignore=1` on a configured Wi-Fi interface, and reconciles those
  settings once per second.

The ELF3-81 acceptance test found a race between alias reconciliation and
NetworkManager during an Ethernet link flap. Before the flap, NetworkManager's
normal `Wired connection 1` profile had obtained `192.168.89.106/16` by DHCP,
and the ZeroLab service had added `192.168.88.213/32` on the same interface.

When the bridge link went down, NetworkManager removed the DHCP address and
cancelled its lease. The ZeroLab supervisor immediately restored the missing
`/32` with `ip address add` while NetworkManager was still reconnecting.
NetworkManager then treated the remaining address as externally supplied,
created a temporary profile whose IPv4 method was `disabled`, and activated it
instead of the DHCP profile. Switching back to `direct` correctly removed the
ZeroLab alias, but the temporary profile then left the Ethernet interface with
no IPv4 address.

The original DHCP profile was intact and was safely restored by activating it.
The defect is therefore in the timing of alias reconciliation, not in DHCP
configuration or rollback ownership.

## Goals

1. Never let ZeroLab alias reconciliation take over an Ethernet interface while
   its ordinary network is disconnected or still recovering.
2. Preserve the customer's DHCP or static address and network-manager profile.
3. Restore a service-owned alias automatically after the ordinary Ethernet
   network is ready again.
4. Preserve the existing ownership snapshot and exact rollback behavior across
   a link flap.
5. Keep `direct`, PD Brake, Normal, ZeroLab entry, ARM, pause, and emergency
   behavior unchanged.
6. Avoid adding a runtime dependency on NetworkManager or a particular profile
   name.

## Non-goals

- Repairing, creating, deleting, modifying, or activating NetworkManager
  profiles.
- Providing DHCP service or choosing an ordinary Ethernet address.
- Supporting an alias-only managed Ethernet interface that intentionally has no
  ordinary IPv4 address. Such a deployment requires a separate, explicitly
  designed standalone policy.
- Changing the sender allowlist, UDP port, ROS domain, tablet mappings, or
  controller state machine.
- Fixing a bridge that does not forward unicast frames. The bridge used in this
  acceptance run still requires separate configuration or replacement.

## Selected Approach

The helper will gate alias address ownership on link readiness using Linux
interface state, without querying or changing NetworkManager.

An alias Ethernet interface is ready only when both conditions hold:

1. its link reports `LOWER_UP`; and
2. it has at least one ordinary IPv4 address other than the configured ZeroLab
   `/32` alias.

The ordinary address may come from DHCP or static configuration. The helper
does not need to know which network manager supplied it.

This approach was selected over two alternatives:

- Querying `nmcli` would couple the helper to NetworkManager, profile names, and
  localized state output.
- Writing the alias into a NetworkManager profile would mutate customer-owned
  persistent configuration and make exact rollback substantially riskier.

## Reconciliation Behavior

### Direct mode

Direct mode is unchanged. It does not inspect link readiness, add or remove
addresses, or modify `arp_ignore`.

### Alias startup

Alias startup continues to validate configuration and create the rollback
snapshot before changing owned state. It records whether the configured alias
was preexisting or is owned by the service.

The helper sets the configured Wi-Fi `arp_ignore` value as before. For the alias
address:

- when the Ethernet interface is ready, startup installs a missing
  service-owned alias;
- when it is not ready, startup leaves the alias absent, retains its ownership
  snapshot, reports readiness to systemd, and lets reconciliation install the
  alias later.

The service must not block candidate hardware startup while DHCP is pending.
An active alias service can therefore temporarily mean "configured and waiting
for the ordinary Ethernet network," not necessarily "UDP path available."
Existing ZeroLab stream freshness gates remain responsible for preventing ARM
without data.

### Alias reconciliation

Each reconciliation cycle validates the saved ownership state before acting.

For an alias whose origin is `added`:

- ready interface plus missing alias: add the exact saved `/32`;
- ready interface plus present alias: make no address change;
- unready interface plus present alias: remove the exact saved `/32` so it
  cannot cause NetworkManager to assume an external connection;
- unready interface plus missing alias: make no address change.

For an alias whose origin is `preexisting`, the helper never removes it. A
missing preexisting alias is restored only when the interface is ready. This
preserves customer ownership without injecting an address during link recovery.

`arp_ignore=1` remains reconciled independently. Losing Ethernet readiness does
not replace the original sysctl snapshot.

### Stop and mode change

Stopping alias mode or changing to direct continues to use the original saved
state:

- a present service-owned alias is removed;
- an absent service-owned alias needs no address action;
- a preexisting alias is preserved, or restored only when safe;
- the original `arp_ignore` value is restored;
- the state directory is removed only after rollback completes.

If safe restoration of a preexisting alias is temporarily impossible because
the interface is not ready, rollback fails closed and retains the snapshot for
a later retry. It must not inject the address during a link transition merely
to complete cleanup.

## Error Handling and Logging

- Initial failure to inspect interface addresses remains fatal because the
  helper cannot safely classify the configured alias as preexisting or
  service-owned.
- After a trusted ownership snapshot exists, failure to inspect link state or
  interface addresses causes no address mutation and is retried. A known
  carrier-down or ordinary-address-missing result is treated as "not ready."
- A failed address add or delete returns a reconciliation failure and is retried
  on the next cycle without replacing the rollback snapshot.
- Logs distinguish waiting, temporary withdrawal, restoration, and command
  failure. Repeated steady-state waiting should not emit a noisy error every
  second.
- Configuration validation and unsafe saved-state failures retain their current
  fail-closed behavior.
- The helper never attempts to recover DHCP itself.

## Testing

Automated tests will extend the fake `ip` implementation with link-state
inspection and cover:

1. alias startup on a ready interface;
2. alias startup with carrier down;
3. alias startup while carrier is up but no ordinary IPv4 address exists;
4. withdrawal of a service-owned alias when readiness is lost;
5. no deletion of a preexisting alias when readiness is lost;
6. restoration after carrier and ordinary IPv4 return;
7. deferral of preexisting-alias restoration until readiness returns;
8. preservation of the original rollback snapshot through withdrawal and
   restoration;
9. retry after link inspection, address add, or address delete failure;
10. no link inspection or network mutation in direct mode;
11. unchanged invalid-configuration and unsafe-state behavior.

Regression verification includes all ZeroLab network and documentation tests,
the 292 SONIC tests, Release builds of both packages, and the C++ A/Y control
rules test.

## Robot Acceptance

ELF3-81 acceptance will run with the controller stacks stopped and the robot
mechanically safe:

1. restore the known normal DHCP profile and verify its ordinary Ethernet IP;
2. enable alias mode and verify the configured `/32` and `arp_ignore=1`;
3. disconnect or power-cycle the bridge;
4. verify the service-owned alias is absent while the ordinary network is down;
5. reconnect the bridge and verify DHCP restores the ordinary address before
   the alias reappears;
6. verify NetworkManager keeps the normal managed profile rather than creating
   or activating an external `disabled` profile;
7. switch to direct and verify exact removal of only service-owned state;
8. verify both controller stacks remain stopped throughout network-only tests.

Strict alias UDP reception remains a separate acceptance item and can pass only
with a bridge that forwards bidirectional unicast traffic.

## Safety Invariants

- ZeroLab never owns or edits the customer's ordinary Ethernet address.
- ZeroLab never edits NetworkManager profiles.
- A service-owned alias cannot remain as the only IPv4 address while the normal
  network is recovering.
- `direct` mode remains network-neutral.
- Lack of an alias or UDP stream cannot bypass `WAIT_STREAM` or `WAIT_ARM`.
- Network-only acceptance never starts a robot controller.
