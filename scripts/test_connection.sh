#!/usr/bin/env bash
set -euo pipefail

echo "======================================"
echo "TChat Connection Test"
echo "======================================"
echo ""
echo "This script will:"
echo "1. Start Server (port 5004)"
echo "2. Wait for you to start Client (port 5006)"
echo "3. Check if connection establishes"
echo ""
echo "Instructions:"
echo "1. Run this script in Terminal 1"
echo "2. Open Terminal 2 and run:"
echo "   ./scripts/run_dev_clean.sh"
echo "   - Set Local Port: 5006"
echo "   - Set Remote IP: 127.0.0.1"  
echo "   - Set Remote Port: 5004"
echo "   - Click 'Call'"
echo ""
echo "Expected behavior:"
echo "- Terminal 1: Status changes to 'Connected'"
echo "- Terminal 2: Status changes to 'Connected'"
echo ""
read -p "Press Enter to start server on port 5004..."

cd "$(dirname "$0")/.."
export GST_DEBUG=2

echo ""
echo "======================================"
echo "Starting Server (Listen mode)"
echo "======================================"
echo "Port: 5004 (RTP), 5005 (Signaling)"
echo ""
echo "Now start the client in another terminal!"
echo "======================================"
echo ""

./scripts/run_dev_clean.sh
