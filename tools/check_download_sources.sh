#!/bin/zsh
set -eu

cd "$(dirname "$0")/.."

echo "=== HomeAIAgent A4.3.2 download source check ==="
echo
echo "Default environment:"
grep -E '^default_envs' platformio.ini || true
echo
echo "Mirrored framework packages:"
grep -A3 '^platform_packages = ' platformio.ini | head -4 || true
echo
echo "Expected large package host:"
echo "  downloads.sourceforge.net"
echo
echo "This script does not download, delete, or modify PlatformIO packages."
