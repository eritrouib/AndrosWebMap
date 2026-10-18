#!/usr/bin/env python3
"""
Download open OpenStreetMap data for Andros (ODbL licence: free to use, credit
"© OpenStreetMap contributors") and save it as osm_andros.json next to this script.

    python fetch_osm.py

Then rebuild the map:
    python build.py --data data --osm osm_andros.json

Only uses the Python standard library. Needs an internet connection.
"""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

BBOX = "37.70,24.66,38.02,25.00"   # Andros only (Tinos and Gyaros fall outside)
QUERY = f"""
[out:json][timeout:180][bbox:{BBOX}];
(
  nwr[amenity~"^(pharmacy|hospital|clinic|doctors|dentist|fuel|police|fire_station|school|kindergarten|post_office|townhall|bus_station|ferry_terminal|parking|drinking_water|fountain|toilets|atm|bank|library|community_centre)$"];
  nwr[healthcare~"^(hospital|clinic|centre|doctor|pharmacy)$"];
  nwr[natural=spring];
  node[highway=bus_stop];
  nwr[tourism~"^(viewpoint|museum)$"];
  nwr[tourism=information][information~"^(office|visitor_centre|board)$"];
  nwr[historic~"^(archaeological_site|castle|tower|ruins|monument)$"];
  nwr[leisure=marina];
);
out center tags;
relation[route=hiking];
out geom tags;
way[highway~"^(path|footway|steps)$"];
out geom tags;
"""
SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("osm_andros.json")
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    for url in SERVERS:
        try:
            print("querying", url, "...")
            req = urllib.request.Request(url, data=body, headers={"User-Agent": "andros-webmap/1.0"})
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.load(r)
            n = len(data.get("elements", []))
            data["fetched"] = time.strftime("%Y-%m-%d")
            out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"saved {n} OSM elements to {out}")
            return
        except Exception as e:  # try the next mirror
            print("  failed:", e)
    sys.exit("All Overpass servers failed. Try again in a few minutes.")


if __name__ == "__main__":
    main()
