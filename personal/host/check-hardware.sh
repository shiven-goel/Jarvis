#!/usr/bin/env bash
# Prints what this machine has and which local Qwen model size fits.
set -uo pipefail
ram_gb=$(awk '/MemTotal/{printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
cpus=$(nproc)
disk_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
gpu=$(lspci 2>/dev/null | grep -iE 'vga|3d' | grep -iE 'nvidia|amd|radeon' | head -1)
vram=""
command -v nvidia-smi >/dev/null && vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
. /etc/os-release 2>/dev/null
py=$(python3 --version 2>/dev/null)

echo "OS:        ${PRETTY_NAME:-unknown}"
echo "CPU cores: $cpus"
echo "RAM:       ${ram_gb} GB"
echo "Free disk: ${disk_gb} GB (in your home folder)"
echo "GPU:       ${gpu:-none detected}${vram:+ ($vram)}"
echo "Python:    ${py:-not installed}  (OpenJarvis needs 3.10-3.13)"
echo
if   [ -n "$vram" ];      then rec="qwen3:8b (or larger, depending on VRAM)"
elif [ "$ram_gb" -ge 16 ]; then rec="qwen3:8b"
elif [ "$ram_gb" -ge 8 ];  then rec="qwen3:4b"
elif [ "$ram_gb" -ge 6 ];  then rec="qwen3:1.7b"
else rec="none: too little RAM. Use cloud models only."; fi
echo "Recommended local model: $rec"
[ "${disk_gb:-0}" -lt 20 ] && echo "WARNING: less than 20 GB free; models and indexes need space."
echo
echo "Copy everything above and send it to Claude."
