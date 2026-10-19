#!/usr/bin/env python3
"""
Download open OpenStreetMap data for Andros (ODbL licence: free to use, credit
"© OpenStreetMap contributors") and save it as osm_andros.json next to this script.

    py fetch_osm.py                 # everything
    py fetch_osm.py --only trails   # just one part, merged into the existing file

Then rebuild the map:
    python build.py --data data --osm osm_andros.json

Only uses the Python standard library. Needs an internet connection.
The public Overpass servers are often busy, so the download is split into three
small requests, each tried on several servers with pauses in between.
"""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

BBOX = "37.70,24.66,38.02,25.00"   # Andros only (Tinos and Gyaros fall outside)
HEAD = f"[out:json][timeout:90][bbox:{BBOX}];"

PARTS = {
    "services and places": HEAD + """
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
out center tags;""",
    "waymarked trails": HEAD + """
relation[route=hiking];
out geom;""",
    "footpaths": HEAD + """
way[highway~"^(path|footway|steps)$"];
out geom tags;""",
}

SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
ROUNDS = 3          # how many times to go round all servers
PAUSE = 20          # seconds between rounds


def run(query):
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for rnd in range(1, ROUNDS + 1):
        for url in SERVERS:
            host = url.split("/")[2]
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "AndrosWebMap/1.0 (etymo.co.uk)"})
                with urllib.request.urlopen(req, timeout=150) as r:
                    data = json.load(r)
                if data.get("remark") and "error" in data["remark"].lower():
                    raise RuntimeError(data["remark"][:120])
                print(f"    ok from {host} ({len(data.get('elements', []))} elements)")
                return data["elements"]
            except Exception as e:
                last = e
                print(f"    {host}: {str(e)[:90]}")
        if rnd < ROUNDS:
            print(f"    all servers busy, waiting {PAUSE}s (round {rnd}/{ROUNDS}) ...")
            time.sleep(PAUSE)
    raise RuntimeError(last)


def main():
    args = sys.argv[1:]
    only = None
    if "--only" in args:
        i = args.index("--only"); only = args[i + 1].lower(); del args[i:i + 2]
    out = Path(args[0]) if args else Path(__file__).with_name("osm_andros.json")
    parts = {k: v for k, v in PARTS.items() if not only or only in k}
    if not parts:
        sys.exit(f"--only must match one of: {', '.join(PARTS)}")
    elements, seen, failed = [], set(), []
    if only and out.exists():   # keep what we already have, replace matching elements
        for e in json.loads(out.read_text(encoding="utf-8"))["elements"]:
            elements.append(e); seen.add((e["type"], e["id"]))
    for name, q in parts.items():
        print(f"downloading {name} ...")
        try:
            for e in run(q):
                key = (e["type"], e["id"])
                if key in seen:
                    elements = [x for x in elements if (x["type"], x["id"]) != key]
                seen.add(key); elements.append(e)
        except Exception:
            failed.append(name)
            print(f"  could not download {name}, skipping it")
    if not elements:
        sys.exit("Nothing downloaded. The Overpass servers are busy: try again later, "
                 "or use the overpass-turbo.eu fallback in the README.")
    out.write_text(json.dumps({"elements": elements, "fetched": time.strftime("%Y-%m-%d")}, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nsaved {len(elements)} OSM elements to {out.name}")
    if failed:
        print("missing parts:", ", ".join(failed), "- run the script again later to fill them in.")


if __name__ == "__main__":
    main()
