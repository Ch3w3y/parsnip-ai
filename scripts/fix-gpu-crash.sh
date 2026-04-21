#!/bin/bash
# fix-gpu-crash.sh — Prevent RX 9070 XT PCIe bus drop
#
# Root cause: amdgpu VCN power-gating bug causes device to fall off the bus.
# Fixes:
#   1. Stop and disable local Ollama (using remote 5070 Ti via Tailscale)
#   2. Set GPU DPM to 'high' (prevents power state transitions)
#   3. Persist DPM setting across reboots
#   4. Add kernel parameters: pcie_aspm=off amdgpu.gpu_recovery=1
#   5. Remove Ollama systemd override
#
# Run with: sudo bash fix-gpu-crash.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

# ── 1. Stop and disable local Ollama ──────────────────────────────────────────
echo ""
echo "=== Step 1: Stop and disable local Ollama ==="
if systemctl is-active ollama.service &>/dev/null; then
    systemctl stop ollama.service && ok "Ollama stopped" || fail "Failed to stop Ollama"
else
    warn "Ollama already stopped"
fi

if systemctl is-enabled ollama.service &>/dev/null; then
    systemctl disable ollama.service && ok "Ollama disabled" || fail "Failed to disable Ollama"
else
    warn "Ollama already disabled"
fi

# ── 2. Set GPU DPM to 'high' ─────────────────────────────────────────────────
echo ""
echo "=== Step 2: Set GPU DPM to 'high' ==="
CARD_PATH="/sys/class/drm/card1/device/power_dpm_force_performance_level"
if [ -f "$CARD_PATH" ]; then
    echo high > "$CARD_PATH" && ok "DPM set to high" || fail "Failed to set DPM"
    CURRENT=$(cat "$CARD_PATH")
    ok "Verified: DPM = $CURRENT"
else
    # Try card0
    CARD_PATH="/sys/class/drm/card0/device/power_dpm_force_performance_level"
    if [ -f "$CARD_PATH" ]; then
        echo high > "$CARD_PATH" && ok "DPM set to high (card0)" || fail "Failed to set DPM"
    else
        fail "Cannot find DPM control file. Check /sys/class/drm/"
    fi
fi

# ── 3. Persist DPM setting across reboots ─────────────────────────────────────
echo ""
echo "=== Step 3: Persist DPM setting ==="
TMPFILE="/etc/tmpfiles.d/amdgpu-dpm-high.conf"
echo "w /sys/class/drm/card1/device/power_dpm_force_performance_level - - - - high" > "$TMPFILE"
ok "Created $TMPFILE"

# ── 4. Add kernel parameters for Limine bootloader ───────────────────────────
echo ""
echo "=== Step 4: Configure Limine bootloader kernel parameters ==="

# Limine on CachyOS uses /boot/limine/limine.conf or /boot/limine.cfg
# Find the config file
LIMINE_CONF=""
for f in /boot/limine/limine.conf /boot/limine.cfg /boot/limine/limine.cfg; do
    if [ -f "$f" ]; then
        LIMINE_CONF="$f"
        break
    fi
done

if [ -z "$LIMINE_CONF" ]; then
    # Search more broadly
    echo "Searching for Limine config..."
    FOUND=$(find /boot -name 'limine*' -type f 2>/dev/null | head -5)
    if [ -n "$FOUND" ]; then
        echo "Found Limine files:"
        echo "$FOUND"
        LIMINE_CONF=$(echo "$FOUND" | grep -E '\.conf$|\.cfg$' | head -1)
    fi
fi

if [ -n "$LIMINE_CONF" ]; then
    # Back up the original
    cp "$LIMINE_CONF" "${LIMINE_CONF}.bak"
    ok "Backed up $LIMINE_CONF → ${LIMINE_CONF}.bak"

    # Check if parameters already exist
    if grep -q 'pcie_aspm=off' "$LIMINE_CONF" 2>/dev/null; then
        warn "pcie_aspm=off already in $LIMINE_CONF"
    else
        # Append to the first /cmdline/ or boot_entry line
        # Limine config format: lines like:
        #   :CachyOS
        #   protocol=linux
        #   cmdline=quiet nowatchdog splash rw rootflags=subvol=/@
        # We need to append pcie_aspm=off amdgpu.gpu_recovery=1 to cmdline lines
        
        if grep -q '^cmdline=' "$LIMINE_CONF" 2>/dev/null; then
            sed -i '/^cmdline=/ s/$/ pcie_aspm=off amdgpu.gpu_recovery=1/' "$LIMINE_CONF"
            ok "Appended kernel parameters to cmdline in $LIMINE_CONF"
        elif grep -q 'cmdline' "$LIMINE_CONF" 2>/dev/null; then
            sed -i '/cmdline/ s/$/ pcie_aspm=off amdgpu.gpu_recovery=1/' "$LIMINE_CONF"
            ok "Appended kernel parameters to cmdline in $LIMINE_CONF"
        else
            warn "Could not find cmdline in $LIMINE_CONF"
            echo "  Please manually add to your Limine config:"
            echo "  pcie_aspm=off amdgpu.gpu_recovery=1"
        fi
    fi
else
    warn "Could not find Limine config file automatically"
    echo ""
    echo "Please manually add these kernel parameters to your Limine config:"
    echo "  pcie_aspm=off amdgpu.gpu_recovery=1"
    echo ""
    echo "Your current kernel cmdline is:"
    cat /proc/cmdline
    echo ""
    echo "The Limine config file is typically at:"
    echo "  /boot/limine/limine.conf"
    echo "  /boot/limine.cfg"
    echo ""
    echo "Look for a 'cmdline=' line and append: pcie_aspm=off amdgpu.gpu_recovery=1"
fi

# ── 5. Remove Ollama systemd override ─────────────────────────────────────────
echo ""
echo "=== Step 5: Remove Ollama systemd override ==="
OVERRIDE="/etc/systemd/system/ollama.service.d/override.conf"
if [ -f "$OVERRIDE" ]; then
    rm "$OVERRIDE" && ok "Removed $OVERRIDE"
    systemctl daemon-reload && ok "Daemon reloaded"
else
    warn "No override file at $OVERRIDE (already clean)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Summary ==="
ok "Ollama stopped and disabled"
ok "GPU DPM set to 'high' (persisted via tmpfiles)"
if [ -n "$LIMINE_CONF" ]; then
    ok "Kernel parameters added to $LIMINE_CONF"
else
    warn "Kernel parameters NOT yet added — manual step required"
fi
ok "Ollama systemd override removed"
echo ""
echo "Reboot to apply all changes. After reboot, verify:"
echo "  cat /proc/cmdline  | grep pcie_aspm=off"
echo "  cat /sys/class/drm/card*/device/power_dpm_force_performance_level"
echo "  systemctl status ollama.service  (should be inactive/disabled)"