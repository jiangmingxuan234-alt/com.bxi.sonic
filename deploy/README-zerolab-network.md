# ZeroLab network mode deployment

Use this guide on the robot that receives ZeroLab UDP on port 18000. The packaged mode is direct: it makes no IP-address or arp_ignore changes. Choose alias only when the customer has explicitly assigned an additional receiver IPv4 address and interfaces.

Do this while the robot is safely supported and with an operator ready to use PD brake. Do not invent an address from another robot, test setup, or this repository.

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

## Back up and install

Set the repository and a dedicated backup destination explicitly. These are customer paths, so the parameter checks intentionally stop an incomplete copy/paste instead of guessing.

~~~bash
set -Eeuo pipefail

REPO_ROOT=${REPO_ROOT:?Set REPO_ROOT to this checked-out repository}
BACKUP_DIR=${BACKUP_DIR:?Set BACKUP_DIR to a new dedicated backup directory}
test -f "$REPO_ROOT/deploy/zerolab-network-config"
test -f "$REPO_ROOT/deploy/config/zerolab-network"
test -f "$REPO_ROOT/deploy/systemd/zerolab-network.service"
test -f "$REPO_ROOT/deploy/systemd/zerolab-hardware.service.d/10-network.conf"

case "$BACKUP_DIR" in
    /*)
        ;;
    *)
        printf 'BACKUP_DIR must be an absolute path: %s\n' "$BACKUP_DIR" >&2
        exit 1
        ;;
esac
BACKUP_PARENT=$(dirname -- "$BACKUP_DIR")
if ! test -d "$BACKUP_PARENT" || test -L "$BACKUP_PARENT"; then
    printf 'BACKUP_DIR parent must be an existing non-symlink directory: %s\n' \
        "$BACKUP_PARENT" >&2
    exit 1
fi
sudo mkdir -- "$BACKUP_DIR"
sudo chown root:root -- "$BACKUP_DIR"
sudo chmod 0700 -- "$BACKUP_DIR"
sudo install -m 0600 /dev/null "$BACKUP_DIR/present"

snapshot_service_state() {
    enabled_state=$(systemctl is-enabled zerolab-network.service 2>/dev/null || true)
    active_state=$(systemctl is-active zerolab-network.service 2>/dev/null || true)
    case "$enabled_state:$active_state" in
        enabled:active|enabled:inactive|disabled:active|disabled:inactive|not-found:inactive)
            ;;
        *)
            printf 'unsupported prior zerolab-network.service state: enabled=%s active=%s\n' \
                "$enabled_state" "$active_state" >&2
            exit 1
            ;;
    esac
    printf '%s\n' "$enabled_state" | sudo tee "$BACKUP_DIR/zerolab-network.enabled" >/dev/null
    printf '%s\n' "$active_state" | sudo tee "$BACKUP_DIR/zerolab-network.active" >/dev/null
}

snapshot_service_state

backup_one() {
    source_file=$1
    backup_name=$2
    if sudo test -e "$source_file" || sudo test -L "$source_file"; then
        sudo cp -a -- "$source_file" "$BACKUP_DIR/$backup_name"
        printf '%s\n' "$backup_name" | sudo tee -a "$BACKUP_DIR/present" >/dev/null
    fi
}

backup_one /etc/default/zerolab-network etc-default-zerolab-network
backup_one /usr/local/libexec/zerolab-network-config zerolab-network-config
backup_one /etc/systemd/system/zerolab-network.service zerolab-network.service
backup_one /etc/systemd/system/zerolab-hardware.service.d/10-network.conf zerolab-hardware-10-network.conf

preserved_sender_line=
if sudo test -e /etc/default/zerolab-network; then
    sender_line_count=$(sudo grep -c '^ZEROLAB_ALLOWED_SENDER=' /etc/default/zerolab-network || true)
    case "$sender_line_count" in
        0)
            ;;
        1)
            preserved_sender_line=$(sudo sed -n '/^ZEROLAB_ALLOWED_SENDER=/p' /etc/default/zerolab-network)
            ;;
        *)
            printf 'expected at most one ZEROLAB_ALLOWED_SENDER line, found %s\n' \
                "$sender_line_count" >&2
            exit 1
            ;;
    esac
fi

if systemctl cat zerolab-network.service >/dev/null 2>&1; then
    sudo systemctl stop zerolab-network.service
fi

sudo install -Dm 0755 "$REPO_ROOT/deploy/zerolab-network-config" /usr/local/libexec/zerolab-network-config
sudo install -Dm 0644 "$REPO_ROOT/deploy/config/zerolab-network" /etc/default/zerolab-network
if [ -n "$preserved_sender_line" ]; then
    sudo sed -i '/^ZEROLAB_ALLOWED_SENDER=/d' /etc/default/zerolab-network
    printf '%s\n' "$preserved_sender_line" | sudo tee -a /etc/default/zerolab-network >/dev/null
fi
sudo install -Dm 0644 "$REPO_ROOT/deploy/systemd/zerolab-network.service" /etc/systemd/system/zerolab-network.service
sudo install -Dm 0644 "$REPO_ROOT/deploy/systemd/zerolab-hardware.service.d/10-network.conf" /etc/systemd/system/zerolab-hardware.service.d/10-network.conf

sudo systemd-analyze verify /etc/systemd/system/zerolab-network.service
sudo systemctl daemon-reload
sudo systemctl enable zerolab-network.service
sudo systemctl restart zerolab-network.service
systemctl is-active zerolab-network.service
~~~

The helper is executable (0755); the configuration, unit, and hardware drop-in are data files (0644). The hardware service only wants and orders after the network service, so a network failure must not prevent PD or Normal from remaining available.

## Sender allowlist

The packaged configuration accepts UDP only from the current ZeroLab sender:

~~~bash
# Packaged/default sender
ZEROLAB_ALLOWED_SENDER=192.168.89.171

# Customer sender
ZEROLAB_ALLOWED_SENDER=192.168.89.200

# Explicitly accept any source IP
ZEROLAB_ALLOWED_SENDER=
~~~

Choose and retain one explicit line in `/etc/default/zerolab-network`. A
repeat installation preserves one existing explicit sender line, including an
intentional empty value; it keeps the packaged default when no sender line
exists and stops before installation if multiple sender lines are present.
This is a source-IP filter only: it does not authenticate source ports or
sender identity. It applies equally in direct and alias modes.

The hardware process reads this configuration when it starts and retains that
start-time environment. After the existing PD Brake, mechanical-support, and
controller-stop procedure, use the manual entry point to start it again:

~~~bash
sudo systemctl start zerolab-hardware.service
~~~

Restarting only `zerolab-network.service` does not apply a changed sender to a
running hardware process.

For foreground development, set the same value before launching the hardware
stack:

~~~bash
export ZEROLAB_ALLOWED_SENDER=192.168.89.200
ros2 launch bxi_example_py_elf3 example_demo_hw.launch.py
~~~

Use `Ctrl+C` to stop the foreground stack. A foreground hardware stack and
the systemd hardware stack must not run together.

## Direct mode

The installation already writes ZEROLAB_NETWORK_MODE=direct. Direct users do not need to change any network configuration. Prove its no-op network contract by recording the actual receiving interfaces before a restart:

~~~bash
set -Eeuo pipefail

ZEROLAB_ETH=${ZEROLAB_ETH:?Set ZEROLAB_ETH to the robot receiving Ethernet interface}
ZEROLAB_WIFI=${ZEROLAB_WIFI:?Set ZEROLAB_WIFI to the robot Wi-Fi interface}

direct_addresses_before=$(ip -4 address show dev "$ZEROLAB_ETH")
direct_arp_ignore_before=$(sysctl -n "net.ipv4.conf.${ZEROLAB_WIFI}.arp_ignore")

sudo systemctl restart zerolab-network.service
systemctl is-active zerolab-network.service
test "$(ip -4 address show dev "$ZEROLAB_ETH")" = "$direct_addresses_before"
test "$(sysctl -n "net.ipv4.conf.${ZEROLAB_WIFI}.arp_ignore")" = "$direct_arp_ignore_before"
~~~

Both test commands must succeed. They prove that direct mode added no address and changed no arp_ignore value.

Find the receiver's existing IPv4 and give the sender that address with UDP port 18000; direct mode needs no additional address.

~~~bash
ip -4 address show
ROBOT_IP=${ROBOT_IP:?Set ROBOT_IP to one existing robot IPv4 address shown above}
ip -4 address show | grep -F -- "inet ${ROBOT_IP}/"
printf 'Configure the sender target as %s:18000\n' "$ROBOT_IP"
~~~

## Alias mode

Use alias mode only after the customer chooses the alias and confirms which Ethernet interface receives UDP and which Wi-Fi interface needs the ARP policy. 10.22.33.44 is only a customer-example value, never a default.

Alias mode requires the receiving Ethernet interface to have carrier and an
ordinary IPv4 address supplied by the customer's DHCP or static configuration.
The ZeroLab service does not edit NetworkManager profiles. While that ordinary
network is unavailable, the service remains active and logs "waiting for ordinary Ethernet network"
without adding its /32. After the ordinary network returns, the supervisor
automatically restores the alias within its next reconciliation cycle.

The ordinary address and the service-owned alias are separate. Verify both
addresses independently before diagnosing a sender or listener problem:

~~~bash
ZEROLAB_ETH=${ZEROLAB_ETH:?Set ZEROLAB_ETH to the Ethernet interface receiving ZeroLab UDP}
ZEROLAB_ALIAS_IP=${ZEROLAB_ALIAS_IP:?Set ZEROLAB_ALIAS_IP to the customer-selected alias}

ip -o -4 address show dev "$ZEROLAB_ETH" | grep -v -F -- "${ZEROLAB_ALIAS_IP}/32"
ip -o -4 address show dev "$ZEROLAB_ETH" | awk '{print $4}' | grep -Fx -- "${ZEROLAB_ALIAS_IP}/32"
~~~

If carrier or the ordinary IPv4 address is lost, the service-owned alias is
temporarily withdrawn while the service remains active; this does not edit the
ordinary network configuration. When the ordinary address returns, the
supervisor restores the alias automatically. Its ownership record is retained,
so switching back to direct mode still removes only an alias added by this
service and restores the prior ARP setting; a pre-existing alias remains.

First record whether that exact address already exists and the previous ARP setting. The service records this ownership, so switching modes restores only state that it owns.

~~~bash
ZEROLAB_ALIAS_IP=${ZEROLAB_ALIAS_IP:?Set ZEROLAB_ALIAS_IP to the customer-selected alias, for example 10.22.33.44}
ZEROLAB_ETH=${ZEROLAB_ETH:?Set ZEROLAB_ETH to the Ethernet interface receiving ZeroLab UDP}
ZEROLAB_WIFI=${ZEROLAB_WIFI:?Set ZEROLAB_WIFI to the Wi-Fi interface for arp_ignore}

alias_was_present=absent
if ip -o -4 address show dev "$ZEROLAB_ETH" | awk '{print $4}' | grep -Fx -- "${ZEROLAB_ALIAS_IP}/32" >/dev/null; then
    alias_was_present=present
fi
alias_arp_ignore_before=$(sysctl -n "net.ipv4.conf.${ZEROLAB_WIFI}.arp_ignore")
~~~

Write the explicit alias configuration, then restart the supervisor:

~~~bash
set -Eeuo pipefail

sender_line_count=$(sudo grep -c '^ZEROLAB_ALLOWED_SENDER=' /etc/default/zerolab-network || true)
test "$sender_line_count" -eq 1
ZEROLAB_ALLOWED_SENDER=$(sudo sed -n 's/^ZEROLAB_ALLOWED_SENDER=//p' /etc/default/zerolab-network)

sudo tee /etc/default/zerolab-network >/dev/null <<EOF
ZEROLAB_NETWORK_MODE=alias
ZEROLAB_ALLOWED_SENDER=$ZEROLAB_ALLOWED_SENDER
ZEROLAB_ALIAS_IP=$ZEROLAB_ALIAS_IP
ZEROLAB_ETH=$ZEROLAB_ETH
ZEROLAB_WIFI=$ZEROLAB_WIFI
EOF

sudo systemctl restart zerolab-network.service
systemctl is-active zerolab-network.service
~~~

Verify the exact /32, configured interface, arp_ignore=1, UDP listener, and journal. If the listener is absent, retain this output before changing anything else.

~~~bash
ip -o -4 address show dev "$ZEROLAB_ETH" | awk '{print $4}' | grep -Fx -- "${ZEROLAB_ALIAS_IP}/32"
sysctl -n "net.ipv4.conf.${ZEROLAB_WIFI}.arp_ignore" | grep -Fx 1
sudo ss -H -lunp 'sport = :18000'
journalctl -u zerolab-network.service --since '-5 minutes' --no-pager
~~~

## Switch back to direct

This mode switch cleans the service's saved alias state. It removes the alias only if this service added it and restores the recorded prior arp_ignore; it preserves a pre-existing alias address.

~~~bash
set -Eeuo pipefail

sender_line_count=$(sudo grep -c '^ZEROLAB_ALLOWED_SENDER=' /etc/default/zerolab-network || true)
test "$sender_line_count" -eq 1
ZEROLAB_ALLOWED_SENDER=$(sudo sed -n 's/^ZEROLAB_ALLOWED_SENDER=//p' /etc/default/zerolab-network)

sudo tee /etc/default/zerolab-network >/dev/null <<EOF
ZEROLAB_NETWORK_MODE=direct
ZEROLAB_ALLOWED_SENDER=$ZEROLAB_ALLOWED_SENDER
EOF

sudo systemctl restart zerolab-network.service
systemctl is-active zerolab-network.service

if ip -o -4 address show dev "$ZEROLAB_ETH" | awk '{print $4}' | grep -Fx -- "${ZEROLAB_ALIAS_IP}/32" >/dev/null; then
    test "$alias_was_present" = present
else
    test "$alias_was_present" = absent
fi
test "$(sysctl -n "net.ipv4.conf.${ZEROLAB_WIFI}.arp_ignore")" = "$alias_arp_ignore_before"
~~~

The final checks prove that only service-owned alias state was rolled back. Do not delete an address manually while the service is active; use the selected mode and restart the service so its state record stays accurate.

## Operational diagnostics and safe behavior

If the network service fails, inspect its status and journal. The non-blocking ordering means PD and Normal remain available. Entering ZeroLab without a usable stream leaves it at WAIT_STREAM; it does not make human targets take over. After enough valid UDP input it reaches WAIT_ARM, and it rejects Y until the operator deliberately arms from WAIT_ARM. Keep the operator neutral and use PD brake for abnormal motion.

~~~bash
systemctl status zerolab-network.service --no-pager
journalctl -u zerolab-network.service --since '-15 minutes' --no-pager
~~~

### Stop the network service

Stopping is a separate fail-closed action and is also the safe first step
before restoring the prior deployment.

~~~bash
set -Eeuo pipefail

sudo systemctl stop zerolab-network.service
~~~

## Restore the backup

Use the same guarded backup directory. The installer records the exact output
of systemctl is-enabled and systemctl is-active in
zerolab-network.enabled and zerolab-network.active. It supports
enabled/disabled paired with active/inactive, plus not-found/inactive for a
fresh installation. Any other pre-install result is rejected before files are
changed. The function restores a file only when it existed before this
deployment; otherwise it removes only that explicitly installed file. It uses
no recursive or wildcard deletion.

~~~bash
set -Eeuo pipefail

BACKUP_DIR=${BACKUP_DIR:?Set BACKUP_DIR to the backup directory created above}
test -d "$BACKUP_DIR"
test ! -L "$BACKUP_DIR"
test -f "$BACKUP_DIR/present"
test -f "$BACKUP_DIR/zerolab-network.enabled"
test -f "$BACKUP_DIR/zerolab-network.active"

previous_enabled=$(sudo cat "$BACKUP_DIR/zerolab-network.enabled")
previous_active=$(sudo cat "$BACKUP_DIR/zerolab-network.active")
case "$previous_enabled:$previous_active" in
    enabled:active|enabled:inactive|disabled:active|disabled:inactive|not-found:inactive)
        ;;
    *)
        printf 'invalid saved zerolab-network.service state: enabled=%s active=%s\n' \
            "$previous_enabled" "$previous_active" >&2
        exit 1
        ;;
esac

# A failed stop exits this block before any helper, unit, or drop-in removal.
sudo systemctl stop zerolab-network.service

# Remove this deployment's enablement before removing an originally disabled
# or absent unit. An originally enabled unit is restored after daemon-reload.
case "$previous_enabled" in
    disabled|not-found)
        sudo systemctl disable zerolab-network.service
        ;;
    enabled)
        ;;
esac

restore_one() {
    backup_name=$1
    target_file=$2
    if sudo grep -Fx -- "$backup_name" "$BACKUP_DIR/present" >/dev/null; then
        sudo cp -a -- "$BACKUP_DIR/$backup_name" "$target_file"
    else
        sudo rm -f -- "$target_file"
    fi
}

restore_one etc-default-zerolab-network /etc/default/zerolab-network
restore_one zerolab-network-config /usr/local/libexec/zerolab-network-config
restore_one zerolab-network.service /etc/systemd/system/zerolab-network.service
restore_one zerolab-hardware-10-network.conf /etc/systemd/system/zerolab-hardware.service.d/10-network.conf

sudo systemctl daemon-reload

restore_service_state() {
    case "$previous_enabled" in
        enabled)
            sudo systemctl enable zerolab-network.service
            ;;
        disabled)
            sudo systemctl disable zerolab-network.service
            ;;
        not-found)
            # The original unit was absent; stop/disable happened before removal.
            return 0
            ;;
    esac
    case "$previous_active" in
        active)
            sudo systemctl start zerolab-network.service
            ;;
        inactive)
            sudo systemctl stop zerolab-network.service
            ;;
    esac
}

restore_service_state
~~~

Keep the backup directory until the restored deployment has been checked. Do not remove service directories: these commands address only the four files installed by this guide.
