#!/bin/bash

# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
#
# SPDX-License-Identifier: BSD-3-Clause

#
# EEMT Web Interface Container Startup Script
# 
# This script handles container initialization including:
# - Cleanup configuration report and an initial dry-run check (no cron in the image)
# - Health check endpoints
# - Application startup
#

set -euo pipefail

echo "Starting EEMT Web Interface container..."

# Report the cleanup configuration. The image has no cron daemon: scheduled cleanup
# runs outside this process (the compose eemt-cleanup service, a Kubernetes CronJob,
# or the host's scheduler calling cleanup_jobs.py).
setup_cleanup() {
    if [[ "${EEMT_CLEANUP_ENABLED:-true}" == "true" ]]; then
        local success_retention=${EEMT_SUCCESS_RETENTION_DAYS:-7}
        local failed_retention=${EEMT_FAILED_RETENTION_HOURS:-12}

        echo "Cleanup configuration:"
        echo "  - Successful job data retention: ${success_retention} days"
        echo "  - Failed job data retention: ${failed_retention} hours"
        echo "  - Schedule: not run in this container (no cron); schedule cleanup_jobs.py externally"

        # Initial cleanup check (dry run, never fatal)
        echo "Running initial cleanup check..."
        python3 /app/cleanup_jobs.py --dry-run > /app/logs/initial_cleanup_check.log 2>&1 || echo "Initial cleanup check completed with warnings"
    else
        echo "Automated cleanup is disabled (EEMT_CLEANUP_ENABLED=false)"
    fi
}

# Function to setup logging directory
setup_logging() {
    echo "Setting up logging..."
    mkdir -p /app/logs
    touch /app/logs/app.log
    touch /app/logs/cleanup.log
    echo "Logging configured"
}

# Function to wait for dependencies
wait_for_dependencies() {
    echo "Checking dependencies..."
    
    # Check if Docker socket is available (for local mode)
    if [[ -S /var/run/docker.sock ]]; then
        echo "Docker socket available - local container mode enabled"
    else
        echo "Docker socket not available - distributed mode or limited functionality"
    fi
    
    # Check Python environment
    python3 -c "import fastapi, sqlite3, pathlib; print('Python dependencies OK')" || {
        echo "ERROR: Python dependencies missing"
        exit 1
    }
    
    echo "Dependencies check complete"
}

# Function to run health check
health_check() {
    echo "Performing health check..."
    
    # Check if app.py can be imported
    python3 -c "import app; print('FastAPI app imports successfully')" || {
        echo "ERROR: FastAPI application import failed"
        return 1
    }
    
    echo "Health check passed"
}

# Main startup sequence
main() {
    echo "EEMT Web Interface Container Startup"
    echo "===================================="
    echo "Container mode: ${EEMT_MODE:-local}"
    echo "Host: ${EEMT_HOST:-0.0.0.0}"
    echo "Port: ${EEMT_PORT:-5000}"
    echo "Cleanup enabled: ${EEMT_CLEANUP_ENABLED:-true}"
    echo ""
    
    # Setup sequence
    setup_logging
    wait_for_dependencies
    health_check
    setup_cleanup
    
    echo ""
    echo "Container initialization complete!"
    echo "Starting FastAPI application..."
    echo ""
    
    # Start the main application
    if [[ "${EEMT_MODE:-local}" == "master" ]]; then
        echo "Starting in master mode with distributed workflow management..."
        # In master mode, we need to start both the web interface and Work Queue master
        exec python3 app.py
    else
        echo "Starting in local mode..."
        exec python3 app.py
    fi
}

# Handle cleanup on container shutdown
cleanup_on_exit() {
    echo "Container shutting down..."
    echo "$(date): Container shutdown" >> /app/logs/app.log || true
    echo "Cleanup complete"
}

# Set trap for cleanup on exit
trap cleanup_on_exit EXIT INT TERM

# Run main function
main "$@"