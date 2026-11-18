#!/bin/bash

# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
#
# SPDX-License-Identifier: BSD-3-Clause

# Monitor EEMT deployment and job execution

echo "🎛️  EEMT Deployment Monitor"
echo "=========================="
echo ""

# Check if container is running
if ! docker ps | grep -q eemt-web-test; then
    echo "❌ EEMT web interface container not running"
    echo "Please run ./test-deployment.sh first"
    exit 1
fi

echo "✅ EEMT Web Interface Status:"
echo "   Container: $(docker ps --filter name=eemt-web-test --format 'table {{.Names}}\t{{.Status}}')"
echo "   URL: http://localhost:5000"
echo ""

# Test API connectivity
if curl -s -f "http://localhost:5000/api/system/status" >/dev/null 2>&1; then
    echo "✅ API Status: Responsive"
else
    echo "⚠️  API Status: Checking connectivity from inside container..."
    RESPONSE=$(docker exec eemt-web-test curl -s "http://localhost:5000/api/system/status" 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "✅ API Status: Working (container-internal)"
    else
        echo "❌ API Status: Not responding"
    fi
fi

echo ""
echo "📊 System Status:"
docker exec eemt-web-test curl -s "http://localhost:5000/api/system/status" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(f'   Docker Available: {data.get(\"docker_available\", \"unknown\")}')
    print(f'   Image: {data.get(\"image_name\", \"unknown\")}')
    print(f'   Mode: Mock (for testing)')
except:
    print('   Status: Unable to parse response')
"

echo ""
echo "📋 Recent Jobs:"
docker exec eemt-web-test curl -s "http://localhost:5000/api/jobs" 2>/dev/null | python3 -c "
import json, sys
try:
    jobs = json.load(sys.stdin)
    if jobs:
        print(f'   Total Jobs: {len(jobs)}')
        for job in jobs[:3]:  # Show last 3 jobs
            status_icon = {'completed': '✅', 'running': '🔄', 'failed': '❌', 'pending': '⏳'}.get(job['status'], '❓')
            print(f'   {status_icon} {job[\"id\"][:8]} | {job[\"workflow_type\"]} | {job[\"status\"]} | {job[\"progress\"]}%')
    else:
        print('   No jobs found')
except:
    print('   Unable to fetch job list')
"

echo ""
echo "📁 Upload Directory:"
echo "   Location: $(pwd)/data/uploads/"
echo "   Contents: $(ls -1 data/uploads/ 2>/dev/null | wc -l) files"
if [ -n "$(ls -A data/uploads/ 2>/dev/null)" ]; then
    echo "   Files:"
    ls -lh data/uploads/ | tail -n +2 | while read line; do
        echo "     $line"
    done
fi

echo ""
echo "🎯 Ready for Testing!"
echo "===================="
echo ""
echo "1. 🌐 Open your browser to: http://localhost:5000"
echo ""
echo "2. 📤 Upload a test DEM:"
echo "   - Click 'Choose File' and select a GeoTIFF (.tif) file"
echo "   - Or drag and drop a DEM file onto the upload area"
echo "   - Sample available: sol/examples/mcn_10m.tif"
echo ""
echo "3. ⚙️  Configure parameters:"
echo "   - Workflow Type: Solar Radiation (default)"
echo "   - Time Step: 15 minutes (recommended)"
echo "   - CPU Threads: 2-4 (for testing)"
echo "   - Linke Turbidity: 3.0 (standard atmosphere)"
echo "   - Surface Albedo: 0.2 (typical soil/vegetation)"
echo ""
echo "4. 🚀 Submit job and monitor progress"
echo ""
echo "5. 📊 Monitor at: http://localhost:5000/monitor"
echo ""
echo "Press Ctrl+C to stop monitoring, or run with --watch for continuous updates"

# Continuous monitoring mode
if [ "$1" = "--watch" ]; then
    echo ""
    echo "🔄 Continuous monitoring mode (press Ctrl+C to stop)..."
    echo "======================================================"
    
    while true; do
        echo ""
        echo "$(date '+%H:%M:%S') - Job Status Check:"
        
        # Get latest jobs
        docker exec eemt-web-test curl -s "http://localhost:5000/api/jobs" 2>/dev/null | python3 -c "
import json, sys
try:
    jobs = json.load(sys.stdin)
    running_jobs = [j for j in jobs if j['status'] == 'running']
    if running_jobs:
        for job in running_jobs:
            print(f'   🔄 {job[\"id\"][:8]} | {job[\"workflow_type\"]} | {job[\"progress\"]}% | {job[\"dem_filename\"]}')
    else:
        completed = len([j for j in jobs if j['status'] == 'completed'])
        failed = len([j for j in jobs if j['status'] == 'failed'])
        print(f'   📊 No active jobs | Completed: {completed} | Failed: {failed}')
except:
    print('   ❌ Unable to check job status')
"
        
        sleep 10
    done
fi