#!/bin/bash
# Repo secret/PII gate (BRIEF.md guardrail 8). Fails on: private keys, common token formats, password/token
# assignments with literal values, MAC addresses other than the documented placeholders, and home-LAN IPs.
# Exempt: facts/ (read-only facts for the builder), docs/ examples, BRIEF.md/GAMEPLAN.md (the brief), this script.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
mapfile -t files < <(git ls-files --cached --others --exclude-standard 2>/dev/null || find . -type f -not -path './.git/*' | sed 's|^\./||')
scan=()
for f in "${files[@]}"; do
    case "$f" in
        facts/*|docs/*|BRIEF.md|GAMEPLAN.md|scripts/check-secrets.sh) continue ;;
        *.png|*.jpg|*.wav|*.ico) continue ;;
    esac
    [ -f "$f" ] && scan+=("$f")
done
hits=0
report() { echo "SECRET-GATE: $1"; printf "    %s\n" "$2"; hits=$((hits + 1)); }

out="$(grep -nE 'BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|xox[baprs]-|sk-ant-|-----BEGIN OPENSSH' "${scan[@]}" 2>/dev/null)"
[ -n "$out" ] && report "private key or token" "$out"

out="$(grep -nEi '(password|passwd|token|secret|api[_-]?key)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'@$<{ ]{4,}["'"'"']' "${scan[@]}" 2>/dev/null |
       grep -vE 'COUCH_[A-Z_]+|@@[A-Z_]+@@|\$\{?[A-Za-z_]+' )"
[ -n "$out" ] && report "literal password/token assignment" "$out"

out="$(grep -noE '\b([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b' "${scan[@]}" 2>/dev/null |
       grep -viE ':(AA[:-]BB[:-]CC[:-]DD[:-]EE[:-]FF|FF[:-]FF[:-]FF[:-]FF[:-]FF[:-]FF|00[:-]00[:-]00[:-]00[:-]00[:-]00|02[:-]00[:-]00[:-]00[:-]00[:-]0[0-9])$')"
[ -n "$out" ] && report "MAC address (use AA:BB:CC:DD:EE:FF)" "$out"

out="$(grep -nE '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b|\b192\.168\.' "${scan[@]}" 2>/dev/null)"
[ -n "$out" ] && report "home LAN address (use gamepc.local)" "$out"

if [ "$hits" -gt 0 ]; then echo "secret gate: $hits finding group(s)"; exit 1; fi
echo "secret gate: clean (${#scan[@]} files)"
