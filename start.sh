#!/bin/sh
# Regenerate dashboard on each container start (prices may have changed)
python generate_dashboard.py
# Then start the server
exec python server.py
