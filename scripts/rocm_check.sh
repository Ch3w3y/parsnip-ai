#!/usr/bin/env bash
# Check local ROCm or remote NVIDIA/Ollama GPU status depending on .env.
set -uo pipefail

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
[ -f "${ROOT_DIR}/.env" ] && set -a && source "${ROOT_DIR}/.env" && set +a

_extract_host() {
    printf '%s' "$1" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##; s#:[0-9]+$##'
}

_is_local_host() {
    case "$1" in
        ""|localhost|127.0.0.1|0.0.0.0|host.docker.internal) return 0 ;;
        *) return 1 ;;
    esac
}

run_remote_check() {
    local ssh_host="$1"
    local ollama_url="$2"
    echo "=== Remote Ollama / CUDA Check ==="
    echo ""

    if ! command -v ssh > /dev/null 2>&1; then
        fail "ssh not found — cannot run remote checks"
        return 1
    fi

    ok "Remote Ollama configured at ${ollama_url}"
    ok "Using SSH target ${ssh_host} for GPU checks"
    echo ""

    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${ssh_host}" "command -v nvidia-smi >/dev/null 2>&1"; then
        ok "nvidia-smi found on remote host"
        ssh -o BatchMode=yes -o ConnectTimeout=5 "${ssh_host}" \
            "nvidia-smi --query-gpu=name,driver_version,cuda_version --format=csv,noheader" 2>/dev/null | \
            sed 's/^/   /'
    else
        warn "nvidia-smi not found on remote host"
    fi
    echo ""

    if ssh -o BatchMode=yes -o ConnectTimeout=5 "${ssh_host}" "systemctl is-active --quiet ollama"; then
        ok "Remote ollama service is active"
    else
        warn "Remote ollama service is not active (or systemd is unavailable)"
    fi
    echo ""

    if curl -sf "${ollama_url}/api/tags" > /dev/null 2>&1; then
        ok "Ollama API is reachable at ${ollama_url}"
    else
        fail "Ollama API is not reachable at ${ollama_url}"
    fi
    echo ""

    echo "Optional manual checks:"
    echo "  ssh ${ssh_host} 'journalctl -u ollama -n 100 --no-pager | grep -Ei \"cuda|nvidia|gpu\"'"
    echo "  curl ${ollama_url}/api/tags"
}

OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_HOST="$(_extract_host "${OLLAMA_URL}")"
REMOTE_SSH_HOST="${OLLAMA_SSH_HOST:-}"

if ! _is_local_host "${OLLAMA_HOST}"; then
    if [ -n "${REMOTE_SSH_HOST}" ]; then
        run_remote_check "${REMOTE_SSH_HOST}" "${OLLAMA_URL}"
        exit 0
    fi

    echo "=== Remote Ollama Check ==="
    echo ""
    ok "Remote Ollama configured at ${OLLAMA_URL}"
    warn "Skipping local ROCm checks because Ollama is not running on this machine"
    warn "Set OLLAMA_SSH_HOST=user@host in .env to enable remote CUDA checks via SSH"
    echo ""
    if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
        ok "Ollama API is reachable"
    else
        fail "Ollama API is not reachable"
    fi
    exit 0
fi

echo "=== Local AMD ROCm / 9070 XT Check ==="
echo ""

# 1. Check rocminfo
if command -v rocminfo > /dev/null 2>&1; then
    ok "rocminfo found"
    GPU_NAME=$(rocminfo 2>/dev/null | grep -m1 "Marketing Name" | sed 's/.*: //')
    GFX_VER=$(rocminfo 2>/dev/null | grep -m1 "gfx" | awk '{print $NF}')
    echo "   GPU: ${GPU_NAME:-unknown}"
    echo "   gfx: ${GFX_VER:-unknown}"
else
    fail "rocminfo not found — ROCm may not be installed"
    echo "   Install: sudo pacman -S rocm-opencl-runtime rocm-hip-sdk  # CachyOS/Arch"
fi
echo ""

# 2. Check HSA override — RDNA 4 (gfx1201) is detected natively; setting this breaks it
if [ -n "${HSA_OVERRIDE_GFX_VERSION:-}" ]; then
    fail "HSA_OVERRIDE_GFX_VERSION=${HSA_OVERRIDE_GFX_VERSION} is SET — this breaks RDNA 4 native detection"
    fail "Remove it from /etc/environment and /etc/fish/config.fish, then reboot."
else
    ok "HSA_OVERRIDE_GFX_VERSION not set (correct for RDNA 4)"
fi
echo ""

# 3. Check Ollama GPU detection
if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    ok "Ollama is reachable at ${OLLAMA_URL}"

    # Quick test embed and check if GPU was used
    EMBED_MODEL="${EMBED_MODEL:-mxbai-embed-large}"
    echo "   Testing embed with ${EMBED_MODEL}…"
    RESULT=$(curl -sf -X POST "${OLLAMA_URL}/api/embed" \
        -d "{\"model\":\"${EMBED_MODEL}\",\"input\":\"test\"}" 2>/dev/null)

    if echo "${RESULT}" | grep -q '"embeddings"'; then
        ok "Embedding working with ${EMBED_MODEL}"

        # Check ollama logs for GPU usage
        if journalctl -u ollama -n 50 --no-pager 2>/dev/null | grep -qi "gpu"; then
            ok "GPU detected in Ollama logs"
        else
            warn "No GPU mention in Ollama logs — may be running on CPU"
            warn "Check: journalctl -u ollama -n 100 | grep -i gpu"
        fi
    else
        fail "Embedding test failed — is ${EMBED_MODEL} pulled?"
        echo "   Run: ollama pull ${EMBED_MODEL}"
    fi
else
    fail "Ollama not reachable at ${OLLAMA_URL}"
    echo "   Start Ollama: ollama serve  (or systemctl start ollama)"
fi
echo ""

# 4. Summary
echo "=== ROCm Notes (RDNA 4 / RX 9070 XT) ==="
echo ""
echo "RDNA 4 (gfx1201) is detected natively by ROCm 6.x."
echo "Do NOT set HSA_OVERRIDE_GFX_VERSION — it breaks native detection on this card."
echo ""
echo "CachyOS-specific: ensure rocm packages are current:"
echo "  sudo pacman -Syu rocm-opencl-runtime rocm-hip-runtime"
