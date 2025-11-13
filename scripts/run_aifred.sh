#!/bin/bash
# AIfred Intelligence Startup Script
# Filters out harmless Reflex write queue error

cd "$(dirname "$0")"
./venv/bin/reflex run 2>&1 | grep -v "Error processing write queue"
