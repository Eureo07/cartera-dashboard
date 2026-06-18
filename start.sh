#!/bin/sh
# Regenerate dashboard in background (takes ~30s, don't block server start)
python generate_dashboard.py &
# Start server immediately
exec python server.py
