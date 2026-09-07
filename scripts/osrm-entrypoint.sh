#!/bin/sh
set -eu

DATA="${OSRM_DATA_FILE:-/data/kullu-manali.osm}"
GRAPH="${DATA%.*}.osrm"

if [ ! -f "${GRAPH}" ]; then
  echo "OSRM graph missing — running extract/partition/customize on ${DATA}"
  if [ ! -f "${DATA}" ]; then
    echo "ERROR: ${DATA} not found. osm-download must run first."
    exit 1
  fi
  osrm-extract -p /opt/car.lua "${DATA}"
  osrm-partition "${GRAPH}"
  osrm-customize "${GRAPH}"
else
  echo "OSRM graph already present at ${GRAPH}"
fi

exec osrm-routed --algorithm mld --max-table-size 10000 "${GRAPH}"
