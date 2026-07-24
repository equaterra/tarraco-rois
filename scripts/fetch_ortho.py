#!/usr/bin/env python3
"""
Fetch orthophotos from WMS/WMTS services (ICGC, IGN PNOA, etc.).

Downloads orthophoto tiles to sandbox/data/ortho/ for use in the pipeline.
Supports GeoTIFF (georeferenced) and JPEG output formats.
"""

import argparse
import io
import math
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

try:
    import pyproj
    HAS_PYPROJ = True
except ImportError:
    HAS_PYPROJ = False


def _transform_4326_to_utm(lon: float, lat: float, utm_zone: int = 31, north: bool = True) -> tuple:
    """Fallback EPSG:4326 to UTM conversion when pyproj is unavailable.

    Uses standard UTM projection formulas.
    """
    # UTM zone central meridian
    central_meridian = (utm_zone - 1) * 6 - 180 + 3

    k0 = 0.9996
    e = 0.00669438
    e2 = e * e
    e3 = e2 * e
    ep2 = e2 / (1 - e2)

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    cm_rad = math.radians(central_meridian)

    N = 6378137 / math.sqrt(1 - e * math.sin(lat_rad) ** 2)
    T = math.tan(lat_rad) ** 2
    C = ep2 * math.cos(lat_rad) ** 2
    A = math.cos(lat_rad) * (lon_rad - cm_rad)

    M = 6378137 * (
        (1 - e / 4 - 3 * e2 / 64 - 5 * e3 / 256) * lat_rad
        - (3 * e / 8 + 3 * e2 / 32 + 45 * e3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2 / 256 + 45 * e3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e3 / 3072) * math.sin(6 * lat_rad)
    )

    x = k0 * N * (A + (1 - T + C) * A**3 / 6 + (5 - 18 * T + T**2 + 72 * C - 58 * ep2) * A**5 / 120) + 500000
    y = k0 * (M + N * math.tan(lat_rad) * (A**2 / 2 + (5 - T + 9 * C + 4 * C**2) * A**4 / 24 + (61 - 58 * T + T**2 + 600 * C - 330 * ep2) * A**6 / 720))

    if not north:
        y += 10000000

    return x, y

# Default WMS services (order = preference)
DEFAULT_SERVICES = {
    "icgc": {
        "name": "ICGC Ortofoto Territorial (Catalunya)",
        "url": "https://geoserveis.icgc.cat/servei/catalunya/orto-territorial/wms",
        "version": "1.3.0",
        "default_layer": "ortofoto_color_vigent",
        "crs": "EPSG:25831",
        "resolution_cm": 25,
        "extent": {"minx": 260000, "miny": 4460000, "maxx": 530000, "maxy": 4760000},
        "notes": "Catalunya only. Layer ortofoto_color_vigent = latest definitive.",
    },
    "ign": {
        "name": "IGN PNOA Maxima Actualidad (Espana)",
        "url": "https://www.ign.es/wms-inspire/pnoa-ma",
        "version": "1.3.0",
        "default_layer": "OI.OrthoimageCoverage",
        "crs": "EPSG:25830",
        "resolution_cm": 25,
        "extent": None,  # All Spain
        "notes": "Spain-wide. Mixed resolution (Sentinel2 at low zoom, PNOA 25cm at high zoom).",
    },
}

SUPPORTED_FORMATS = {
    "geotiff": "image/tiff",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
}

# Default output directory
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sandbox" / "data" / "ortho"


def _ssl_context():
    """Create SSL context that works on all platforms."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_capabilities(service_key: str) -> dict:
    """Fetch and parse WMS GetCapabilities for a service.

    Returns dict with service info and available layers.
    """
    svc = DEFAULT_SERVICES.get(service_key)
    if not svc:
        raise ValueError(f"Unknown service: {service_key}. Available: {list(DEFAULT_SERVICES.keys())}")

    caps_url = f"{svc['url']}?SERVICE=WMS&REQUEST=GetCapabilities&VERSION={svc['version']}"
    ctx = _ssl_context()

    try:
        resp = urllib.request.urlopen(caps_url, timeout=30, context=ctx)
        xml_data = resp.read().decode("utf-8")
    except Exception as e:
        raise ConnectionError(f"Failed to fetch capabilities from {svc['name']}: {e}")

    root = ET.fromstring(xml_data)
    ns = {"wms": "http://www.opengis.net/wms"}

    layers = {}
    for layer_elem in root.iter("{http://www.opengis.net/wms}Layer"):
        name_elem = layer_elem.find("wms:Name", ns)
        if name_elem is None:
            continue
        name = name_elem.text
        bbox_elem = layer_elem.find("wms:BoundingBox", ns)
        if bbox_elem is not None:
            crs = bbox_elem.get("CRS", "")
            minx = float(bbox_elem.get("minx", 0))
            miny = float(bbox_elem.get("miny", 0))
            maxx = float(bbox_elem.get("maxx", 0))
            maxy = float(bbox_elem.get("maxy", 0))
            layers[name] = {"crs": crs, "bbox": [minx, miny, maxx, maxy]}

    return {"service": svc["name"], "url": svc["url"], "layers": layers}


def _to_utm(lon: float, lat: float, target_crs: str) -> tuple:
    """Transform EPSG:4326 coordinates to target UTM CRS.

    Uses pyproj if available, otherwise falls back to manual calculation.
    """
    if HAS_PYPROJ:
        transformer = pyproj.Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
        return transformer.transform(lon, lat)

    # Extract UTM zone from CRS string (e.g., EPSG:25831 -> zone 31N)
    import re
    m = re.match(r"EPSG:(\d+)", target_crs)
    if not m:
        raise ValueError(f"Cannot parse CRS: {target_crs}. Install pyproj for arbitrary CRS support.")

    epsg = int(m.group(1))
    # ETRS89 UTM north: 25800+zone
    if 25801 <= epsg <= 25860:
        zone = epsg - 25800
        north = True
    # WGS84 UTM north: 32600+zone
    elif 32601 <= epsg <= 32660:
        zone = epsg - 32600
        north = True
    # WGS84 UTM south: 32700+zone
    elif 32701 <= epsg <= 32760:
        zone = epsg - 32700
        north = False
    else:
        raise ValueError(f"Cannot auto-detect UTM zone for EPSG:{epsg}. Install pyproj.")

    return _transform_4326_to_utm(lon, lat, zone, north)


def check_point_in_extent(lat: float, lon: float, service_key: str = None) -> dict:
    """Check which services cover a given EPSG:4326 point.

    Args:
        lat: Latitude (EPSG:4326)
        lon: Longitude (EPSG:4326)
        service_key: Optional filter to a specific service

    Returns:
        Dict of service_key -> {covers: bool, native_crs: str, bbox_in_crs: list}
    """
    results = {}
    services_to_check = [service_key] if service_key else DEFAULT_SERVICES.keys()

    for key in services_to_check:
        svc = DEFAULT_SERVICES[key]
        info = {"covers": False, "native_crs": svc["crs"], "notes": svc["notes"]}

        if svc["extent"] is None:
            info["covers"] = True
            results[key] = info
            continue

        # Transform point to native CRS
        try:
            x, y = _to_utm(lon, lat, svc["crs"])
            ext = svc["extent"]
            info["covers"] = ext["minx"] <= x <= ext["maxx"] and ext["miny"] <= y <= ext["maxy"]
            info["point_in_crs"] = [round(x, 2), round(y, 2)]
        except Exception as e:
            info["error"] = str(e)

        results[key] = info

    return results


def fetch_ortho(
    center_lat: float,
    center_lon: float,
    size_m: int = 1000,
    output_format: str = "geotiff",
    service_key: str = None,
    layer: str = None,
    output_dir: Path = None,
    output_name: str = None,
    source_crs: str = "EPSG:4326",
) -> Path:
    """Download an orthophoto tile from a WMS service.

    Args:
        center_lat: Center latitude (default EPSG:4326)
        center_lon: Center longitude (default EPSG:4326)
        size_m: Size of the square area in meters (default 1000)
        output_format: One of geotiff, jpeg, png
        service_key: Which service to use (icgc, ign). Auto-detect if None.
        layer: WMS layer name. Uses default for the service if None.
        output_dir: Where to save. Default: sandbox/data/ortho/
        output_name: Output filename (without extension). Auto-generated if None.
        source_crs: CRS of input coordinates (default EPSG:4326)

    Returns:
        Path to the downloaded file.
    """

    # Auto-detect service based on location
    if service_key is None:
        coverage = check_point_in_extent(center_lat, center_lon)
        for key, info in coverage.items():
            if info.get("covers"):
                service_key = key
                break
        if service_key is None:
            raise ValueError(
                f"Point ({center_lat}, {center_lon}) not covered by any configured service. "
                f"Coverage: {coverage}"
            )
        print(f"Auto-detected service: {service_key} ({DEFAULT_SERVICES[service_key]['name']})")

    svc = DEFAULT_SERVICES[service_key]

    # Transform center to native CRS
    if source_crs != svc["crs"]:
        cx, cy = _to_utm(center_lon, center_lat, svc["crs"])
    else:
        cx, cy = center_lon, center_lat

    # Calculate BBOX
    half = size_m / 2.0
    minx = cx - half
    miny = cy - half
    maxx = cx + half
    maxy = cy + half

    # Calculate pixel dimensions (25cm resolution)
    resolution_m = svc["resolution_cm"] / 100.0
    width_px = int(size_m / resolution_m)
    height_px = int(size_m / resolution_m)

    # Determine layer
    if layer is None:
        layer = svc["default_layer"]

    # Determine MIME type
    fmt_key = output_format.lower().replace(".", "")
    mime_type = SUPPORTED_FORMATS.get(fmt_key, "image/tiff")

    # Build WMS GetMap URL
    # Note: In EPSG:25831/25830, BBOX order is minx,miny,maxx,maxy (x,y)
    bbox_str = f"{minx},{miny},{maxx},{maxy}"
    params = (
        f"SERVICE=WMS&VERSION={svc['version']}&REQUEST=GetMap"
        f"&LAYERS={layer}&STYLES="
        f"&CRS={svc['crs']}"
        f"&BBOX={bbox_str}"
        f"&WIDTH={width_px}&HEIGHT={height_px}"
        f"&FORMAT={mime_type}"
        f"&TRANSPARENT=FALSE"
    )
    url = f"{svc['url']}?{params}"

    print(f"Service:    {svc['name']}")
    print(f"Layer:      {layer}")
    print(f"CRS:        {svc['crs']}")
    print(f"BBOX:       {bbox_str}")
    print(f"Pixels:     {width_px}x{height_px}")
    print(f"Area:       {size_m}m x {size_m}m ({svc['resolution_cm']}cm/px)")
    print(f"Format:     {mime_type}")
    print(f"URL length: {len(url)} chars")

    # Download
    ctx = _ssl_context()
    print(f"\nDownloading from {svc['url']}...")
    try:
        resp = urllib.request.urlopen(url, timeout=120, context=ctx)
        data = resp.read()
    except Exception as e:
        raise ConnectionError(f"Download failed: {e}")

    # Determine output path
    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ext_map = {"image/tiff": ".tif", "image/jpeg": ".jpg", "image/png": ".png"}
    ext = ext_map.get(mime_type, ".tif")

    if output_name is None:
        # Generate name from coordinates
        output_name = f"ortho_{service_key}_{center_lat:.4f}_{center_lon:.4f}_{size_m}m"

    out_path = output_dir / f"{output_name}{ext}"

    with open(out_path, "wb") as f:
        f.write(data)

    size_kb = len(data) / 1024
    size_mb = size_kb / 1024
    print(f"\nSaved: {out_path}")
    print(f"Size:  {size_kb:.1f} KB ({size_mb:.2f} MB)")

    # Validate
    if len(data) < 1000:
        try:
            error_text = data.decode("utf-8", errors="replace")
            if "ServiceException" in error_text:
                print(f"\nWARNING: WMS error response:\n{error_text[:500]}")
        except Exception:
            pass

    return out_path


def validate_download(file_path: Path) -> dict:
    """Validate a downloaded orthophoto file.

    Returns dict with validation results.
    """
    path = Path(file_path)
    results = {"exists": path.exists(), "size_bytes": 0, "format": "unknown", "valid": False}

    if not path.exists():
        return results

    results["size_bytes"] = path.stat().st_size
    results["size_kb"] = results["size_bytes"] / 1024

    with open(path, "rb") as f:
        header = f.read(16)

    if header[:2] == b"\xff\xd8":
        results["format"] = "JPEG"
        results["valid"] = True
    elif header[:4] == b"II\x2a\x00" or header[:4] == b"MM\x00\x2a":
        results["format"] = "GeoTIFF"
        results["valid"] = True
    elif header[:4] == b"\x89PNG":
        results["format"] = "PNG"
        results["valid"] = True
    elif b"ServiceException" in header:
        results["format"] = "WMS Error"
        results["valid"] = False

    # Try to get dimensions with PIL if available
    try:
        from PIL import Image

        img = Image.open(path)
        results["width"] = img.width
        results["height"] = img.height
        results["mode"] = img.mode
    except ImportError:
        pass
    except Exception as e:
        results["pil_error"] = str(e)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Download orthophotos from WMS services (ICGC, IGN PNOA)."
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # fetch command
    fetch_p = sub.add_parser("fetch", help="Download an orthophoto tile")
    fetch_p.add_argument("lat", type=float, help="Center latitude (EPSG:4326)")
    fetch_p.add_argument("lon", type=float, help="Center longitude (EPSG:4326)")
    fetch_p.add_argument("--size", type=int, default=1000, help="Area size in meters (default: 1000)")
    fetch_p.add_argument("--format", choices=["geotiff", "jpeg", "png"], default="geotiff")
    fetch_p.add_argument("--service", choices=["icgc", "ign"], default=None, help="WMS service (auto-detect)")
    fetch_p.add_argument("--layer", default=None, help="WMS layer name")
    fetch_p.add_argument("--output-dir", type=Path, default=None, help="Output directory")
    fetch_p.add_argument("--output-name", default=None, help="Output filename (without ext)")

    # check command
    check_p = sub.add_parser("check", help="Check service capabilities")
    check_p.add_argument("--service", choices=["icgc", "ign"], default=None, help="Service to check")
    check_p.add_argument("--lat", type=float, default=None, help="Check if point is covered")
    check_p.add_argument("--lon", type=float, default=None)

    # validate command
    val_p = sub.add_parser("validate", help="Validate a downloaded file")
    val_p.add_argument("file", type=Path, help="File to validate")

    args = parser.parse_args()

    if args.command == "fetch":
        out = fetch_ortho(
            center_lat=args.lat,
            center_lon=args.lon,
            size_m=args.size,
            output_format=args.format,
            service_key=args.service,
            layer=args.layer,
            output_dir=args.output_dir,
            output_name=args.output_name,
        )
        val = validate_download(out)
        print(f"\nValidation: {val}")

    elif args.command == "check":
        if args.lat and args.lon:
            results = check_point_in_extent(args.lat, args.lon, args.service)
            for key, info in results.items():
                status = "COVERS" if info.get("covers") else "NO COVERAGE"
                print(f"  {key}: {status} ({DEFAULT_SERVICES[key]['name']})")
                if "point_in_crs" in info:
                    print(f"    Point in {info['native_crs']}: {info['point_in_crs']}")
        else:
            services = [args.service] if args.service else DEFAULT_SERVICES.keys()
            for key in services:
                caps = get_capabilities(key)
                print(f"\n{caps['service']}:")
                print(f"  URL: {caps['url']}")
                print(f"  Layers: {len(caps['layers'])}")
                for name, info in list(caps["layers"].items())[:10]:
                    print(f"    {name}: {info['crs']} {info['bbox']}")

    elif args.command == "validate":
        val = validate_download(args.file)
        for k, v in val.items():
            print(f"  {k}: {v}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
