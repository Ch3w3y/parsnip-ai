#!/usr/bin/env bash
# One-shot setup: generate an age keypair for encrypting backup secrets.
# Stores the private key at ~/.config/parsnip/age.key (chmod 600) and prints
# the public key for inclusion in .env as AGE_RECIPIENT.
#
# Usage:
#   ./scripts/setup_age_key.sh                # generate (refuses to overwrite)
#   ./scripts/setup_age_key.sh --print-public # just print the public key
#   ./scripts/setup_age_key.sh --rotate       # generate new key, KEEP OLD as .key.bak
set -euo pipefail

KEY_DIR="${HOME}/.config/parsnip"
KEY_PATH="${KEY_DIR}/age.key"

require_age() {
    if ! command -v age-keygen >/dev/null 2>&1; then
        cat >&2 <<'EOF'
ERROR: age-keygen not found. Install with one of:
  apt install age          # Debian/Ubuntu
  dnf install age          # Fedora
  pacman -S age            # Arch
  brew install age         # macOS
EOF
        exit 1
    fi
}

print_public() {
    if [[ ! -f "$KEY_PATH" ]]; then
        echo "ERROR: no key at $KEY_PATH" >&2
        exit 1
    fi
    age-keygen -y "$KEY_PATH"
}

generate() {
    local rotate=$1
    require_age
    install -d -m 0700 "$KEY_DIR"

    if [[ -f "$KEY_PATH" ]]; then
        if [[ "$rotate" == "yes" ]]; then
            mv "$KEY_PATH" "${KEY_PATH}.bak.$(date +%s)"
            echo "Old key moved to ${KEY_PATH}.bak.* — keep until all backups using it are expired."
        else
            echo "ERROR: key already exists at $KEY_PATH. Use --rotate to replace." >&2
            exit 1
        fi
    fi

    age-keygen -o "$KEY_PATH" 2>/dev/null
    chmod 600 "$KEY_PATH"
    PUBLIC=$(age-keygen -y "$KEY_PATH")

    cat <<EOF

==============================================================================
  age key generated
==============================================================================

  Private key file: $KEY_PATH       (chmod 600 — DO NOT commit, DO NOT lose)
  Public key:       $PUBLIC

NEXT STEPS — IMPORTANT:

  1. Back up the private key to your password manager / 1Password / Bitwarden.
     If you lose this file, encrypted secrets in GCS become UNRECOVERABLE.

  2. Add the public key to your project .env file:

       AGE_RECIPIENT=$PUBLIC

  3. Run a config backup to verify encryption works end-to-end:

       uv run scripts/backup_config.py --local

==============================================================================
EOF
}

case "${1:-generate}" in
    --print-public|-p) print_public ;;
    --rotate)          generate yes ;;
    generate|"")       generate no ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
