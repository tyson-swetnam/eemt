#!/bin/bash

# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
#
# SPDX-License-Identifier: BSD-3-Clause

# Script to rebuild EEMT Docker container with latest code changes
#
# The workflow image is tagged eemt:<docker/VERSION> by docker/build.sh.

echo "====================================="
echo "EEMT Container Rebuild Script"
echo "====================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed or not in PATH${NC}"
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &> /dev/null; then
    echo -e "${RED}Error: Docker daemon is not running${NC}"
    exit 1
fi

cd "$(dirname "${BASH_SOURCE[0]}")"   # repository root
WORKFLOW_IMAGE="eemt:$(tr -d '[:space:]' < docker/VERSION)"
WEB_IMAGE="eemt-web:$(tr -d '[:space:]' < docker/VERSION)"

echo -e "${YELLOW}Step 1: Stopping any running EEMT containers...${NC}"
docker ps -q --filter "ancestor=${WORKFLOW_IMAGE}" | xargs -r docker stop
docker ps -q --filter "ancestor=${WEB_IMAGE}" | xargs -r docker stop

echo -e "${YELLOW}Step 2: Removing old containers...${NC}"
docker ps -aq --filter "ancestor=${WORKFLOW_IMAGE}" | xargs -r docker rm
docker ps -aq --filter "ancestor=${WEB_IMAGE}" | xargs -r docker rm

echo -e "${YELLOW}Step 3: Building workflow container ${WORKFLOW_IMAGE}...${NC}"
docker/build.sh
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to build base container${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 4: Building web interface container ${WEB_IMAGE}...${NC}"
docker/build.sh web --build-arg "EEMT_DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 999)"
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to build web interface container${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 5: Verifying container builds...${NC}"
if docker image inspect "${WORKFLOW_IMAGE}" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Base container built successfully${NC}"
else
    echo -e "${RED}✗ Base container not found${NC}"
    exit 1
fi

if docker image inspect "${WEB_IMAGE}" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Web interface container built successfully${NC}"
else
    echo -e "${RED}✗ Web interface container not found${NC}"
    exit 1
fi

echo -e "${YELLOW}Step 6: Testing container functionality...${NC}"
# Test the workflow container (versions, GRASS, GDAL, CCTools, one r.sun day, a local Makeflow)
docker run --rm --network none --user "$(id -u):$(id -g)" "${WORKFLOW_IMAGE}" eemt-selftest
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ eemt-selftest passed in the workflow container${NC}"
else
    echo -e "${RED}✗ eemt-selftest failed${NC}"
fi

# Test workflow script
docker run --rm "${WORKFLOW_IMAGE}" python /opt/eemt/bin/run-solar-workflow.py --help
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Solar workflow script is accessible${NC}"
else
    echo -e "${RED}✗ Solar workflow script test failed${NC}"
fi

echo ""
echo -e "${GREEN}====================================="
echo "Container rebuild complete!"
echo "=====================================${NC}"
echo ""
echo "To start the web interface, run:"
echo "  docker-compose up"
echo ""
echo "Or manually:"
echo "  docker run -p 5000:5000 -v \$(pwd)/data:/app/data ${WEB_IMAGE}"