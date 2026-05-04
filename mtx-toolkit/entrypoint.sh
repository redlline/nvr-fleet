#!/bin/sh
# Entrypoint for mtx-toolkit-frontend
# Auth is handled by the main NVR Fleet nginx proxy at /monitor/
# This script is intentionally minimal - just log and exit cleanly
echo "[mtx-toolkit] Frontend starting (auth handled by upstream nginx)"
