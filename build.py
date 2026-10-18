#!/usr/bin/env python3
"""
Andros web map - build script.

Reads the original GIS project data (shapefiles in ΕΓΣΑ87 / EPSG:2100 plus the
13 m DTM and hillshade), converts everything for the web and writes ONE
self-contained HTML file (Leaflet) that can be put on any web server, e.g.
the Municipality's Joomla site, with no GeoServer / database needed.

Usage:
    python build.py --data ../data --out andros_webmap.html

Requirements: geopandas, rasterio, numpy, pillow
"""
import argparse, base64, io, json, math, unicodedata, warnings
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin
from PIL import Image

warnings.filterwarnings("ignore")
HERE = Path(__file__).parent

# --------------------------------------------------------------------------- helpers
GR2LAT = {  # simplified ELOT 743 transliteration (for village names that only exist in Greek)
    "αι": "ai", "ει": "ei", "οι": "oi", "ου": "ou", "αυ": "av", "ευ": "ev", "μπ": "b", "ντ": "nt", "γγ": "ng", "γκ": "gk",
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k", "λ": "l",
    "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f",
    "χ": "ch", "ψ": "ps", "ω": "o",
}


def translit(s: str) -> str:
    if not s:
        return s
    base = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    out, i = [], 0
    low = base.lower()
    while i < len(low):
        two = low[i:i + 2]
        if two in GR2LAT:
            t = GR2LAT[two]; n = 2
        else:
            t = GR2LAT.get(low[i], low[i]); n = 1
        if base[i].isupper() and t:
            t = t[0].upper() + t[1:]
        out.append(t); i += n
    return "".join(out)


def rnd(geom, nd=5):
    """Round coordinates of a GeoJSON geometry dict (keeps the file small)."""
    def r(c):
        if isinstance(c[0], (list, tuple)):
            return [r(x) for x in c]
        return [round(c[0], nd), round(c[1], nd)]
    geom["coordinates"] = r(geom["coordinates"])
    return geom


def to_fc(gdf, props_fn, simplify=None):
    gdf = gdf.copy()
    if simplify:
        gdf["geometry"] = gdf.geometry.simplify(simplify, preserve_topology=True)
    gdf = gdf.to_crs(4326)
    feats = []
    for _, row in gdf.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        feats.append({"type": "Feature", "properties": props_fn(row),
                      "geometry": rnd(json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0]["geometry"])})
    return {"type": "FeatureCollection", "features": feats}


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v).strip() or None


# --------------------------------------------------------------------------- vectors
def build_vectors(d50: Path, d150: Path):
    rd = lambda p: gpd.read_file(p)
    vill = rd(d50 / "oikismoi.shp")
    vill150 = set(rd(d150 / "oikismoi.shp")["oikismoi"].str.strip())  # generalised set = "main" villages
    layers = {}
    TOP = ("Γαύριο", "Μπατσί", "Όρμος Κορθίου", "Αρνάς", "Παλαιόπολη")  # always labelled

    def nearest_village(geom):
        dists = vill.geometry.distance(geom)
        return vill.loc[dists.idxmin(), "oikismoi"].strip()

    layers["villages"] = to_fc(vill, lambda r: {
        "el": r.oikismoi.strip(), "en": translit(r.oikismoi.strip()),
        "main": r.oikismoi.strip() in vill150 or r.oikismoi.strip() in TOP,
        "top": r.oikismoi.strip() in TOP})
    # Χώρα gets a proper label
    for f in layers["villages"]["features"]:
        if f["properties"]["el"] == "Άνδρος":
            f["properties"].update(el="Άνδρος (Χώρα)", en="Andros (Chora)", main=True, capital=True)

    layers["beaches"] = to_fc(rd(d50 / "beach.shp"), lambda r: {"el": clean(r["Όνομα"]), "en": clean(r["Name"])})

    rel = rd(d50 / "rel_mon.shp")
    layers["monasteries"] = to_fc(rel[rel.Type == 1], lambda r: {"el": clean(r["Όνομα"]), "en": clean(r["Name"])})
    layers["churches"] = to_fc(rel[rel.Type == 2], lambda r: {"el": clean(r["Όνομα"]), "en": clean(r["Name"])})

    mon = rd(d50 / "monuments.shp")
    layers["monuments"] = to_fc(mon, lambda r: {"near": nearest_village(r.geometry), "code": int(r["Είδος"])})

    banks = rd(d50 / "banks.shp")
    layers["banks"] = to_fc(banks, lambda r: {"el": clean(r["Όνομα"]), "en": clean(r["Name"]),
                                              "addr": clean(r["Address"]), "plus": clean(r["Plus_Code"])})

    def near_layer(name):
        g = rd(d50 / f"{name}.shp")
        g = g[~g.geometry.apply(lambda p: (round(p.x / 50), round(p.y / 50))).duplicated()]  # drop duplicate points (e.g. wrecks)
        return to_fc(g, lambda r: {"near": nearest_village(r.geometry)})

    for k, f in [("ports", "port"), ("lighthouses", "lighthouse"), ("wrecks", "wreck"),
                 ("caves", "cave"), ("camping", "camping"), ("factories", "factory")]:
        layers[k] = near_layer(f)

    layers["peaks"] = to_fc(rd(d50 / "elevpnt.shp"), lambda r: {"h": round(float(r.ELEVATION))})

    roads = rd(d50 / "road_net.shp")
    layers["roads"] = to_fc(roads, lambda r: {"c": int(r["Eίδος"])}, simplify=4)

    hyd = rd(d50 / "hydro_net.shp")
    hyd["named"] = hyd["Όνομα"].notna()
    layers["rivers"] = to_fc(hyd[hyd.named], lambda r: {"el": clean(r["Όνομα"]), "en": clean(r["Name"]),
                                                       "kind": clean(r["Είδος"])}, simplify=5)
    layers["streams"] = to_fc(hyd[~hyd.named], lambda r: {}, simplify=8)

    iso = rd(d50 / "iso20.shp")
    layers["contours"] = to_fc(iso, lambda r: {"h": int(r.Elevation)}, simplify=6)
    layers["coast"] = to_fc(rd(d50 / "coast.shp"), lambda r: {}, simplify=3)
    return layers


# --------------------------------------------------------------------------- rasters
RAMP = [  # elevation (m) -> colour; green island, schist & marble uplands
    (0, (214, 222, 186)), (60, (196, 211, 166)), (200, (167, 191, 139)),
    (400, (150, 168, 120)), (600, (170, 160, 128)), (800, (178, 166, 146)), (1000, (226, 220, 210)),
]


def tint(dem):
    xs = np.array([r[0] for r in RAMP], float)
    out = np.zeros(dem.shape + (3,), float)
    for i in range(3):
        out[..., i] = np.interp(dem, xs, [r[1][i] for r in RAMP])
    return out


def build_relief(dtm_p: Path, hs_p: Path, merc_res=20.0):
    with rasterio.open(dtm_p) as src:
        dem = src.read(1).astype("float32")
        nod = src.nodata
        src_t, src_crs = src.transform, src.crs
        bounds = src.bounds
    with rasterio.open(hs_p) as src:
        hs = src.read(1).astype("float32")
    land = (dem > 0.5) & (dem < 5000) & (dem != nod)
    dem[~land] = 0
    hs[~land] = 180

    rgb = tint(dem)
    shade = np.clip(hs / 180.0, 0, 1.25)
    shade = 0.55 + 0.45 * shade                         # soften the hillshade
    rgb = np.clip(rgb * shade[..., None], 0, 255)
    rgba = np.dstack([rgb, land * 255.0]).astype("float32")

    # warp to Web Mercator so Leaflet can stretch it linearly
    l, b, r, t = transform_bounds(src_crs, "EPSG:3857", *bounds)
    W, H = int((r - l) / merc_res), int((t - b) / merc_res)
    dst_t = from_origin(l, t, merc_res, merc_res)
    out = np.zeros((4, H, W), "float32")
    for i in range(4):
        reproject(rgba[..., i], out[i], src_transform=src_t, src_crs=src_crs,
                  dst_transform=dst_t, dst_crs="EPSG:3857", resampling=Resampling.bilinear)
    img = Image.fromarray(np.moveaxis(out, 0, -1).clip(0, 255).astype("uint8"), "RGBA")
    buf = io.BytesIO(); img.save(buf, "WEBP", quality=82, method=6)
    lonlat = transform_bounds("EPSG:3857", "EPSG:4326", l, b, r, t)
    return base64.b64encode(buf.getvalue()).decode(), [[lonlat[1], lonlat[0]], [lonlat[3], lonlat[2]]]


def build_elevation_grid(dtm_p: Path, step=0.0004):
    """DTM resampled to a regular lon/lat grid, uint16 decimetres -> base64 (for click / profile lookups)."""
    with rasterio.open(dtm_p) as src:
        dem = src.read(1).astype("float32"); nod = src.nodata
        dem[(dem == nod) | (dem < 0) | (dem > 5000)] = 0
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        W, H = int((e - w) / step) + 1, int((n - s) / step) + 1
        out = np.zeros((H, W), "float32")
        reproject(dem, out, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=from_origin(w, n, step, step), dst_crs="EPSG:4326", resampling=Resampling.bilinear)
    arr = np.clip(out * 10, 0, 65535).astype("<u2")
    return {"w": w, "n": n, "step": step, "cols": W, "rows": H,
            "data": base64.b64encode(arr.tobytes()).decode()}



# --------------------------------------------------------------------------- OpenStreetMap (optional)
def osm_cat(t):
    a, h = t.get("amenity"), t.get("healthcare")
    if a == "pharmacy" or h == "pharmacy": return "pharmacy"
    if a == "hospital" or h == "hospital": return "hospital"
    if a in ("clinic", "doctors", "dentist") or h in ("clinic", "centre", "doctor"): return "clinic"
    if a == "police": return "police"
    if a == "fire_station": return "fire"
    if a in ("school", "kindergarten"): return "school"
    if a in ("library", "community_centre"): return "civic"
    if a == "post_office": return "post"
    if a == "townhall": return "townhall"
    if a in ("atm", "bank"): return "atm"
    if a == "bus_station" or t.get("highway") == "bus_stop": return "bus"
    if a == "ferry_terminal" or t.get("leisure") == "marina": return "ferry"
    if a == "fuel": return "fuel"
    if a == "parking": return "parking"
    if a == "toilets": return "toilets"
    if a in ("drinking_water", "fountain") or t.get("natural") == "spring": return "water"
    tour, hist = t.get("tourism"), t.get("historic")
    if tour == "viewpoint": return "viewpoint"
    if tour == "museum": return "museum"
    if tour == "information": return "info"
    if hist in ("castle", "tower"): return "castle"
    if hist in ("archaeological_site", "ruins", "monument"): return "archaeo"
    return None


def hav(a, b):
    R = 6371008.8; r = math.pi / 180
    dp, dl = (b[1] - a[1]) * r, (b[0] - a[0]) * r
    x = math.sin(dp / 2) ** 2 + math.cos(a[1] * r) * math.cos(b[1] * r) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def chain(parts):
    """Join way segments of a route into as few ordered lines as possible."""
    parts = [p for p in parts if len(p) > 1]
    if not parts: return []
    out = [parts.pop(0)]
    while parts:
        cur = out[-1]; best = None
        for i, p in enumerate(parts):
            for rev in (False, True):
                q = p[::-1] if rev else p
                for at_end in (True, False):
                    d = hav(cur[-1], q[0]) if at_end else hav(q[-1], cur[0])
                    if best is None or d < best[0]: best = (d, i, rev, at_end)
        d, i, rev, at_end = best
        q = parts.pop(i); q = q[::-1] if rev else q
        if d < 30:
            out[-1] = cur + q[1:] if at_end else q[:-1] + cur
        else:
            out.append(q)
    return out


def simplify_ll(coords, tol=0.00003):
    from shapely.geometry import LineString
    if len(coords) < 3: return coords
    return [list(c) for c in LineString(coords).simplify(tol).coords]


def build_osm(path: Path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    pois, trails, paths, trail_ways = [], [], [], set()
    for e in raw["elements"]:
        if e["type"] == "relation" and e.get("tags", {}).get("route") == "hiking":
            parts = [[[round(p["lon"], 5), round(p["lat"], 5)] for p in m.get("geometry", [])]
                     for m in e.get("members", []) if m["type"] == "way"]
            trail_ways |= {m["ref"] for m in e.get("members", []) if m["type"] == "way"}
            lines = [simplify_ll(l, 0.00001) for l in chain(parts)]
            if not lines: continue
            t = e["tags"]; L = sum(hav(a, b) for l in lines for a, b in zip(l, l[1:]))
            nm = t.get("name") or (f"Διαδρομή {t['ref']}" if t.get("ref") else "Μονοπάτι")
            trails.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines},
                           "properties": {"el": nm, "en": t.get("name:en") or translit(nm), "ref": t.get("ref"),
                                          "km": round(L / 1000, 2), "web": t.get("website"), "osm": e["id"]}})
    for e in raw["elements"]:
        t = e.get("tags", {})
        if e["type"] == "way" and t.get("highway") in ("path", "footway", "steps") and "geometry" in e:
            if e["id"] in trail_ways: continue
            c = simplify_ll([[round(p["lon"], 5), round(p["lat"], 5)] for p in e["geometry"]])
            paths.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": c}, "properties": {}})
            continue
        if e["type"] == "relation" and t.get("route") == "hiking": continue
        cat = osm_cat(t)
        if not cat: continue
        lat = e.get("lat") or e.get("center", {}).get("lat"); lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None: continue
        nm = t.get("name")
        pois.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                     "properties": {"cat": cat, "el": nm, "en": t.get("name:en") or (translit(nm) if nm else None),
                                    "phone": t.get("phone") or t.get("contact:phone"), "hours": t.get("opening_hours"),
                                    "web": t.get("website") or t.get("contact:website"), "osm": f"{e['type']}/{e['id']}"}})
    for f in pois:  # drop empty keys
        f["properties"] = {k: v for k, v in f["properties"].items() if v}
    return {"pois": {"type": "FeatureCollection", "features": pois},
            "trails": {"type": "FeatureCollection", "features": trails},
            "paths": {"type": "FeatureCollection", "features": paths},
            "fetched": raw.get("fetched")}

# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--osm", default=None, help="optional osm_andros.json made by fetch_osm.py")
    ap.add_argument("--out", default=str(HERE / "index.html"))
    a = ap.parse_args()
    D = Path(a.data)
    d50, d150 = D / "scale50000/scale50000", D / "scale150000/scale150000"
    dtm, hs = D / "raster/raster/dtm_androstif1.tif", D / "raster/raster/HillSha_tif11.tif"

    layers = build_vectors(d50, d150)
    if a.osm:
        layers["osm"] = build_osm(Path(a.osm))
    relief_b64, relief_bounds = build_relief(dtm, hs)
    grid = build_elevation_grid(dtm)

    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    css = (HERE / "leaflet.css").read_text(encoding="utf-8")
    html = (tpl.replace("/*__LEAFLET_CSS__*/", css).replace("/*__LAYERS__*/null", json.dumps(layers, ensure_ascii=False, separators=(",", ":")))
               .replace("/*__RELIEF_BOUNDS__*/null", json.dumps(relief_bounds))
               .replace("__RELIEF_B64__", relief_b64)
               .replace("/*__GRID__*/null", json.dumps(grid)))
    Path(a.out).write_text(html, encoding="utf-8")
    counts = {k: len(v["features"]) for k, v in layers.items() if "features" in v}
    if "osm" in layers:
        from collections import Counter
        counts["osm"] = dict(Counter(f["properties"]["cat"] for f in layers["osm"]["pois"]["features"]),
                             trails=len(layers["osm"]["trails"]["features"]), paths=len(layers["osm"]["paths"]["features"]))
    print(f"wrote {a.out}  ({len(html)/1e6:.1f} MB)\n", counts)


if __name__ == "__main__":
    main()
