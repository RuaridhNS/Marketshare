#!/usr/bin/env python3
"""Fetch world coastlines once and decode them to plain lon/lat rings.

The globe needs land to be readable, but the artifact's CSP blocks runtime
fetches and the page should not carry a TopoJSON decoder for a file that never
changes. So this is a build step: it downloads Natural Earth 110m land (via
world-atlas), decodes the quantised delta-encoded arcs here, and writes plain
rings to data/land_110m.json, which is committed and read by the exporter.

Coordinates are rounded to 2dp - about a kilometre, and far finer than a
600px globe can draw - which cuts the file by more than half.

    python scripts/fetch_land.py
"""
import json, pathlib, urllib.request

URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json"
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "land_110m.json"


def decode_arcs(topo):
    """Quantised delta-encoded arcs -> absolute [lon, lat] point lists."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append([round(x * sx + tx, 2), round(y * sy + ty, 2)])
        out.append(pts)
    return out


def stitch(arc_ids, arcs):
    """A ring is a list of arc indices; a negative index means that arc
    reversed, and the shared endpoint is dropped where arcs join."""
    ring = []
    for i in arc_ids:
        pts = arcs[~i][::-1] if i < 0 else arcs[i]
        ring.extend(pts[1:] if ring else pts)
    return ring


def main():
    print("fetching", URL)
    topo = json.load(urllib.request.urlopen(URL, timeout=60))
    arcs = decode_arcs(topo)
    geom = topo["objects"]["land"]["geometries"]

    rings = []
    for g in geom:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        for poly in polys:
            for r in poly:                     # ring 0 is outer, rest are holes
                ring = stitch(r, arcs)
                if len(ring) >= 4:
                    rings.append(ring)

    # drop rings too small to see on a globe - specks of rock, not landmasses
    def span(ring):
        xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
        return max(max(xs) - min(xs), max(ys) - min(ys))
    big = [r for r in rings if span(r) > 0.8]

    OUT.write_text(json.dumps(big, separators=(",", ":")), encoding="utf-8")
    pts = sum(len(r) for r in big)
    print(f"rings {len(rings)} -> {len(big)} kept, {pts:,} points, "
          f"{OUT.stat().st_size/1024:.0f} KB -> {OUT}")


if __name__ == "__main__":
    main()
