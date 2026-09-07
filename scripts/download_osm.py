"""Download a highway-only OSM extract for the Kullu-Manali demo corridor.

Used by the osrm-init container so the first `docker compose up` can build a
local OSRM graph without committing a binary PBF.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

# south, west, north, east — NH-3 Kullu to Manali (Himachal Pradesh)
BBOX = (31.90, 77.02, 32.30, 77.32)
OUT_PATH = os.environ.get("OSM_OUT", "/data/kullu-manali.osm")

QUERY = f"""[out:xml][timeout:180];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|motorway_link|trunk_link|primary_link|secondary_link|track)$"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
(._;>;);
out body;
"""

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def already_ready(path: str) -> bool:
    if os.path.exists(path) and os.path.getsize(path) > 50_000:
        return True
    pbf = path.replace(".osm", ".osm.pbf")
    return os.path.exists(pbf) and os.path.getsize(pbf) > 20_000


def download() -> None:
    if already_ready(OUT_PATH):
        print(f"OSM extract already present at {OUT_PATH}", flush=True)
        return

    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    last_error = None
    encoded = QUERY.encode("utf-8")

    for attempt in range(4):
        for url in ENDPOINTS:
            print(f"Downloading OSM extract (attempt {attempt + 1}) from {url}", flush=True)
            req = urllib.request.Request(
                url,
                data=encoded,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "APCS-SIH26037/0.1 (hackathon prototype)",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=200) as resp:
                    body = resp.read()
                if b"<osm" not in body[:2000] and b"<?xml" not in body[:200]:
                    raise RuntimeError(f"Unexpected Overpass response ({len(body)} bytes)")
                tmp = OUT_PATH + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(body)
                os.replace(tmp, OUT_PATH)
                print(f"Wrote {len(body)} bytes to {OUT_PATH}", flush=True)
                return
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                print(f"Failed: {exc}", flush=True)
                time.sleep(8)

    raise SystemExit(f"Could not download OSM extract: {last_error}")


if __name__ == "__main__":
    try:
        download()
    except KeyboardInterrupt:
        sys.exit(1)
