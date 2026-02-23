#!/bin/bash
# ============================================================
# SESLY BOT - Docker Entrypoint
# ============================================================
# Sets up virtual display and audio before starting the app

set -e

echo "=== Sesly Bot Container Starting ==="

# ============================================================
# PULSEAUDIO SETUP
# ============================================================
echo "[AUDIO] Starting PulseAudio..."

# PulseAudio config for root user
mkdir -p /run/pulse /root/.config/pulse
chmod 777 /run/pulse

# PulseAudio'yu root olarak çalıştırmak için config
cat > /root/.config/pulse/client.conf << EOF
default-server = unix:/run/pulse/native
autospawn = no
EOF

cat > /root/.config/pulse/daemon.conf << EOF
exit-idle-time = -1
flat-volumes = no
EOF

# Kill any existing PulseAudio
pulseaudio --kill 2>/dev/null || true
sleep 1

# Start PulseAudio with explicit runtime path
export PULSE_RUNTIME_PATH=/run/pulse
pulseaudio \
    --daemonize=yes \
    --exit-idle-time=-1 \
    --system=false \
    --disallow-exit=yes \
    --log-target=stderr \
    --log-level=error \
    2>/dev/null || true

# Wait for PulseAudio
sleep 2

# Verify PulseAudio is running
if pulseaudio --check 2>/dev/null; then
    echo "[AUDIO] PulseAudio started successfully"
else
    echo "[AUDIO] PulseAudio failed with default, trying alternative..."
    # Alternative: start without restrictions
    export PULSE_SERVER=unix:/tmp/pulse-socket
    pulseaudio \
        --daemonize=yes \
        --exit-idle-time=-1 \
        --disallow-exit=yes \
        --log-target=stderr \
        --log-level=error \
        --use-pid-file=false \
        2>/dev/null || true
    sleep 2
fi

# Create virtual audio sink (for capturing browser audio)
echo "[AUDIO] Creating virtual audio sink..."
pactl load-module module-null-sink sink_name=virtual_mic sink_properties=device.description="VirtualMic" 2>/dev/null || true

# Set virtual_mic as default sink so browser audio goes there
pactl set-default-sink virtual_mic 2>/dev/null || true

# Create loopback to route audio
pactl load-module module-loopback source=virtual_mic.monitor 2>/dev/null || true

echo "[AUDIO] Audio setup complete"

# Verify audio setup
echo "[AUDIO] Sinks:"
pactl list short sinks 2>/dev/null || echo "  (none)"
echo "[AUDIO] Sources:"
pactl list short sources 2>/dev/null || echo "  (none)"

# ============================================================
# XVFB SETUP (Virtual Display)
# ============================================================
echo "[DISPLAY] Starting Xvfb..."

# Kill any existing Xvfb
pkill -f "Xvfb :99" 2>/dev/null || true
sleep 1

# Start Xvfb on display :99
Xvfb :99 -screen 0 1920x1080x24 -ac &
XVFB_PID=$!

# Wait for Xvfb to be ready
sleep 2

# Verify Xvfb is running
if kill -0 $XVFB_PID 2>/dev/null; then
    echo "[DISPLAY] Xvfb started successfully (PID: $XVFB_PID)"
else
    echo "[ERROR] Xvfb failed to start!"
    exit 1
fi

export DISPLAY=:99

# ============================================================
# READY
# ============================================================
echo "[READY] Container ready!"

# Execute the passed command (default: python server.py)
exec "$@"
