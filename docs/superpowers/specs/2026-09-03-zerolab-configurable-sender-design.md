# ZeroLab Configurable Sender Design

## Goal

Allow each customer to select the IPv4 address permitted to send ZeroLab UDP
frames without rebuilding or editing the installed Mod. Preserve
`192.168.89.171` as the packaged default, retain a fail-closed explicit way to
disable the source-address filter, and leave the existing PICO path unchanged.

## Configuration contract

The deployment configuration at `/etc/default/zerolab-network` gains one
setting:

```bash
ZEROLAB_ALLOWED_SENDER=192.168.89.171
```

The effective sender is resolved when the ZeroLab source node is constructed:

1. If `ZEROLAB_ALLOWED_SENDER` is present in the process environment, its value
   overrides the Mod manifest.
2. If the variable is absent, the existing `mod.yaml` `allowed_sender` value is
   used. This preserves the current `192.168.89.171` behavior for deployments
   that have not installed the new configuration.
3. The exact lowercase keyword `any` disables source-IP filtering and accepts
   UDP datagrams from any sender, subject to the existing packet-size and
   protocol checks.

Every other value must be an exact IPv4 address. Empty values, IPv6 addresses,
hostnames, malformed addresses, and values with surrounding whitespace are
rejected before the UDP receiver is created. Invalid configuration must never
silently weaken the allowlist. The explicit keyword avoids systemd
`EnvironmentFile` normalizing an accidental whitespace-only value into the
previous empty allow-all sentinel. Because systemd also strips surrounding
whitespace from unquoted non-empty values, the deployed hardware path must
validate the raw configuration line before systemd's normalized environment is
allowed to reach the controller. Thus `ZEROLAB_ALLOWED_SENDER=any` is accepted,
while `ZEROLAB_ALLOWED_SENDER= any`, `ZEROLAB_ALLOWED_SENDER=any `, quoted
variants, and duplicate assignments fail closed.

The setting filters only the UDP source address. It does not authenticate the
sender, restrict its source port, or change the destination port `18000`.

## Runtime integration

Sender resolution lives in a small standard-library-only ZeroLab module. This
keeps environment handling and IPv4 validation independent from ROS, ZMQ, PICO,
and the receiver implementation. `zerolab.source_node` passes the resolved
value to the existing `ZeroLabUdpReceiver`; the receiver's accept/drop behavior
does not change.

The existing `zerolab-hardware.service` network drop-in gains:

```ini
[Service]
EnvironmentFile=-/etc/default/zerolab-network
ExecStartPre=/usr/local/libexec/zerolab-network-config validate-sender /etc/default/zerolab-network
```

The environment file is optional so an older or incomplete installation still
falls back to the manifest default. `zerolab-network.service` continues to read
the same file for direct/alias networking, but network mode and sender
allowlisting remain independent settings.

The existing root-owned `zerolab-network-config` helper implements the
`validate-sender` preflight. It reads the explicit path passed by the unit rather
than accepting an environment-controlled path override. A missing file or an
absent sender assignment preserves the manifest fallback. If a sender
assignment is present, there must be exactly one active assignment and its raw
line must have no leading/trailing whitespace, quotes, or spacing around `=`.
The value must be either the exact lowercase `any` sentinel or an IPv4 address.
Malformed sender-looking assignments and duplicate assignments fail the
preflight. Python resolution remains the final validation layer before the UDP
socket is opened.

Configuration is process-start scoped. Changing the file does not mutate a
running hardware process. An operator must use the existing safety procedure to
stop the hardware controller and then start it manually again. Restarting only
`zerolab-network.service` does not apply a new sender value to a running
controller and must not start hardware.

For the original foreground launch workflow, an operator may export
`ZEROLAB_ALLOWED_SENDER` before `ros2 launch`. The foreground launch remains
stoppable with `Ctrl+C`; it must not run concurrently with the systemd hardware
stack.

## PICO isolation

This change does not edit the PICO manifest contract: `pico_manager`,
`smpl_bridge`, `sonic_teleop`, `activate`, `reset_alignment`, the existing PICO
routes, and the existing PICO action remain unchanged. The environment override
is read only when `zerolab_source` is constructed in `sonic_zerolab`.

PICO continues to use its existing local endpoints, while ZeroLab continues to
use its separate UDP input and intermediate local endpoint. An invalid ZeroLab
sender setting may prevent the ZeroLab source from starting, but it must not
change the PICO state, PICO processes, PICO ports, or PICO network selection.

## Deployment and operator workflow

The packaged configuration keeps the secure current default. A customer changes
only the value in `/etc/default/zerolab-network`, for example:

```bash
ZEROLAB_NETWORK_MODE=direct
ZEROLAB_ALLOWED_SENDER=192.168.89.200
```

The exact lowercase keyword is the deliberate opt-out:

```bash
ZEROLAB_ALLOWED_SENDER=any
```

Deployment documentation will explain the default, custom IPv4, empty-value
rejection, `any` behavior, validation failures, process restart requirement, direct/alias
independence, and the foreground environment override. Robot commands remain
guarded by the existing controller-stop, mechanical-support, and physical
emergency-stop procedures.

## Error handling

- A valid IPv4 address is canonicalized before comparison.
- The exact lowercase `any` override maps to no source-address filter.
- An absent override preserves the manifest value.
- An empty or otherwise invalid override raises a clear configuration error
  before the UDP socket is opened.
- The systemd hardware path rejects raw configuration spelling that systemd
  would otherwise normalize into the valid `any` sentinel.
- A raw sender validation failure prevents the manually requested hardware
  service from starting; it does not start, stop, enable, or disable any other
  controller service.
- No error path falls back from an invalid value to accepting arbitrary
  senders.
- No configuration path starts, stops, enables, or disables a hardware
  controller.

## Verification

Automated tests will prove:

1. An absent environment variable uses the manifest sender.
2. A custom IPv4 environment value overrides the manifest sender.
3. The exact lowercase `any` environment value disables the filter.
4. Empty, invalid, IPv6, hostname, and whitespace-padded values are rejected
   without opening the
   receiver.
5. The UDP receiver continues accepting the configured sender and counting a
   different sender as unexpected.
6. The packaged default contains both direct network mode and
   `ZEROLAB_ALLOWED_SENDER=192.168.89.171`.
7. The hardware drop-in reads `/etc/default/zerolab-network`, while the network
   service retains its existing behavior and no reverse hardware dependency is
   introduced.
8. The hardware preflight accepts an exact IPv4 and exact lowercase `any`, but
   rejects padded, quoted, empty, malformed, and duplicate raw assignments
   before systemd normalization can weaken the policy.
9. Existing PICO manifest entries are unchanged and all existing automated
   tests pass.

Local verification must not start robot hardware. Separate guarded robot
acceptance will verify a custom sender, rejection of a different sender, the
explicit `any` opt-out if required, and the complete PICO TCP/body-tracking/calibration/
live-pose/head/wrist/gripper/re-entry path.

## Acceptance criteria

The implementation is accepted when a customer can configure a fixed sender
IPv4 without rebuilding the Mod, the current sender remains the default,
invalid values fail closed, PICO configuration remains unchanged, automated
tests pass, and no hardware service is enabled or started by the change.
