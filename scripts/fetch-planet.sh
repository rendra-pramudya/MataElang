#!/usr/bin/env bash
# Download the Protomaps planet build (~120 GB) to data/tiles/planet.pmtiles.
# Check https://maps.protomaps.com/builds/ for the current daily file and pass its URL.
set -euo pipefail
cd "$(dirname "$0")/.."
URL="${1:?usage: scripts/fetch-planet.sh <build-url from https://maps.protomaps.com/builds/>}"
mkdir -p data/tiles
wget -c -O data/tiles/planet.pmtiles "$URL"
