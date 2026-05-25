#!/usr/bin/env python3
"""
Business Finder - No Website Detector
======================================
Finds businesses within a radius of a location that have NO website listed.

Uses:
  - Nominatim (OpenStreetMap) for geocoding  — FREE, no API key
  - Overpass API (OpenStreetMap) for business data — FREE, no API key
  - Optional: Google search scraping to double-check for websites

Usage:
  python find_businesses_no_website.py

Requirements:
  pip install requests geopy rich
"""

import csv
import re
import sys
import time
from datetime import datetime

# ── Dependency check with clear install instructions ──────────────────────────
_MISSING = []
try:
    import requests
except ImportError:
    _MISSING.append("requests")

try:
    from geopy.geocoders import Nominatim
    from geopy.distance import geodesic
except ImportError:
    _MISSING.append("geopy")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
except ImportError:
    _MISSING.append("rich")

if _MISSING:
    print("Missing required packages. Install them with:")
    print(f"  pip install {' '.join(_MISSING)}")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
USER_AGENT    = "BusinessNoWebsiteFinder/2.0 (local-research-tool)"
REQUEST_DELAY = 1.5          # seconds between API calls (polite rate limiting)
MAX_RETRIES   = 3            # retry attempts for network calls
RETRY_BACKOFF = 2.0          # seconds; multiplied by attempt number
MIN_RADIUS_KM = 0.1
MAX_RADIUS_KM = 50.0

# Multiple Overpass mirrors — tried in order if one fails
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Domains to skip when scanning Google results
IGNORED_DOMAINS = frozenset([
    "google.", "youtube.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "yelp.com", "tripadvisor.com",
    "maps.google", "googleapis.com", "gstatic.com", "accounts.google",
    "support.google", "policies.google", "webcache.", "translate.google",
    "ggpht.com", "apple.com", "linkedin.com", "foursquare.com",
    "yellowpages.com", "bbb.org", "mapquest.com", "pinterest.com",
    "wikipedia.org", "wikimedia.org",
])

console = Console()


# ── Generic retry helper ──────────────────────────────────────────────────────

def _retry(func, *args, retries=MAX_RETRIES, backoff=RETRY_BACKOFF, **kwargs):
    """
    Call func(*args, **kwargs) up to `retries` times.
    Waits backoff * attempt seconds between tries.
    Returns (result, True) on success, (None, False) after total failure.
    """
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs), True
        except Exception as exc:
            wait = backoff * attempt
            if attempt < retries:
                console.print(
                    f"  [yellow]Attempt {attempt}/{retries} failed ({exc}). "
                    f"Retrying in {wait:.0f}s…[/yellow]"
                )
                time.sleep(wait)
            else:
                console.print(f"  [red]All {retries} attempts failed: {exc}[/red]")
    return None, False


# ── Input helpers ─────────────────────────────────────────────────────────────

def _prompt_nonempty(prompt):
    """Keep asking until the user enters a non-blank string."""
    while True:
        value = console.input(prompt).strip()
        if value:
            return value
        console.print("[yellow]Input cannot be empty. Please try again.[/yellow]")


def _prompt_float(prompt, lo, hi, default):
    """Keep asking until the user enters a float in [lo, hi], or hits Enter for default."""
    while True:
        raw = console.input(prompt).strip()
        if not raw:
            console.print(f"[yellow]Using default: {default} km[/yellow]")
            return default
        try:
            value = float(raw)
        except ValueError:
            console.print("[yellow]Please enter a number (e.g. 2.5).[/yellow]")
            continue
        if not (lo <= value <= hi):
            console.print(f"[yellow]Radius must be between {lo} and {hi} km.[/yellow]")
            continue
        return value


def _prompt_yes_no(prompt):
    """Keep asking until the user answers y or n."""
    while True:
        raw = console.input(prompt).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        console.print("[yellow]Please enter y or n.[/yellow]")


# ── Geocoding ─────────────────────────────────────────────────────────────────

def _do_geocode(location_str):
    geolocator = Nominatim(user_agent=USER_AGENT)
    result = geolocator.geocode(location_str, timeout=10)
    if result is None:
        raise ValueError(f"No geocoding results for '{location_str}'")
    return result.latitude, result.longitude


def geocode_location(location_str):
    """
    Convert a place name / address to (lat, lon).
    Returns None if geocoding ultimately fails after retries.
    """
    console.print(f"[cyan]Geocoding '{location_str}'…[/cyan]")
    coords, ok = _retry(_do_geocode, location_str)
    if not ok or coords is None:
        console.print(
            "[red]Could not geocode that location.\n"
            "Tips: add a city/state/country, use a zip code, or check spelling.[/red]"
        )
        return None
    return coords


# ── Overpass fetching ─────────────────────────────────────────────────────────

def _build_overpass_query(lat, lon, radius_m):
    r = radius_m
    return f"""
[out:json][timeout:90];
(
  node["name"]["shop"](around:{r},{lat},{lon});
  node["name"]["amenity"](around:{r},{lat},{lon});
  node["name"]["office"](around:{r},{lat},{lon});
  node["name"]["tourism"](around:{r},{lat},{lon});
  node["name"]["leisure"](around:{r},{lat},{lon});
  node["name"]["craft"](around:{r},{lat},{lon});
  node["name"]["healthcare"](around:{r},{lat},{lon});
  way["name"]["shop"](around:{r},{lat},{lon});
  way["name"]["amenity"](around:{r},{lat},{lon});
  way["name"]["office"](around:{r},{lat},{lon});
);
out center tags;
"""


def _do_overpass_request(mirror, query):
    resp = requests.post(
        mirror,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if "elements" not in data:
        raise ValueError("Overpass response missing 'elements' key")
    return data["elements"]


def fetch_businesses(lat, lon, radius_m):
    """
    Query Overpass with mirror fallback and per-mirror retries.
    Returns a (possibly empty) list of raw OSM elements.
    """
    query = _build_overpass_query(lat, lon, radius_m)
    console.print(f"[cyan]Querying Overpass API (radius: {radius_m:,} m)…[/cyan]")

    for mirror in OVERPASS_MIRRORS:
        console.print(f"  [dim]Trying: {mirror}[/dim]")
        elements, ok = _retry(_do_overpass_request, mirror, query)
        if ok and elements is not None:
            console.print(f"  [green]Success — {len(elements):,} elements received.[/green]")
            return elements
        console.print("  [yellow]Mirror failed, trying next…[/yellow]")
        time.sleep(REQUEST_DELAY)

    console.print(
        "[red]All Overpass mirrors failed.\n"
        "Check your internet connection or try again later.[/red]"
    )
    return []


# ── Parsing ───────────────────────────────────────────────────────────────────

def _first_tag(*keys, tags, fallback=""):
    """Return the first non-empty string value among the given OSM tag keys."""
    for key in keys:
        val = tags.get(key, "")
        if isinstance(val, str):
            val = val.strip()
        if val:
            return val
    return fallback


def build_address(tags):
    parts = []
    for key in ("addr:housenumber", "addr:street", "addr:city",
                "addr:postcode", "addr:state"):
        val = tags.get(key, "").strip()
        if val:
            parts.append(val)
    return ", ".join(parts)


def parse_business(element, center_lat, center_lon):
    """
    Extract structured fields from one OSM element.
    Returns None if the element cannot be used.
    """
    if not isinstance(element, dict):
        return None

    tags = element.get("tags")
    if not isinstance(tags, dict):
        return None

    name = tags.get("name", "").strip()
    if not name:
        return None

    elem_type = element.get("type", "")
    if elem_type == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")

    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None   # element has no usable coordinates

    try:
        distance_m = round(geodesic((center_lat, center_lon), (lat, lon)).meters)
    except Exception:
        distance_m = -1   # unknown distance; element still included

    biz_type = _first_tag(
        "shop", "amenity", "office", "tourism",
        "leisure", "craft", "healthcare",
        tags=tags, fallback="business",
    )
    website = _first_tag("website", "contact:website", "url", "contact:url", tags=tags)
    phone   = _first_tag("phone", "contact:phone", "contact:mobile", tags=tags)
    email   = _first_tag("email", "contact:email", tags=tags)

    return {
        "name":               name,
        "type":               biz_type,
        "lat":                lat,
        "lon":                lon,
        "distance_m":         distance_m,
        "website":            website,
        "phone":              phone,
        "email":              email,
        "address":            build_address(tags),
        "opening_hours":      tags.get("opening_hours", "").strip(),
        "google_website_found": "",
        "osm_id":             str(element.get("id", "")),
        "osm_type":           elem_type,
    }


# ── Google scrape ─────────────────────────────────────────────────────────────

_GOOGLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_URL_PATTERN = re.compile(r'href="(https?://[^"&]{6,})"')
_google_rate_limited = False   # set to True once Google sends a 429


def _is_ignored_url(url):
    lower = url.lower()
    return any(bad in lower for bad in IGNORED_DOMAINS)


def _do_google_search(query):
    url = (
        "https://www.google.com/search"
        f"?q={requests.utils.quote(query)}&num=5&hl=en"
    )
    resp = requests.get(url, headers=_GOOGLE_HEADERS, timeout=12, allow_redirects=True)
    if resp.status_code == 429:
        raise PermissionError("Google rate limit (429)")
    resp.raise_for_status()

    for match in _URL_PATTERN.finditer(resp.text):
        candidate = match.group(1)
        if not _is_ignored_url(candidate):
            return candidate
    return None   # no suitable URL found in results


def check_website_via_google(business_name, location_hint):
    """
    Search Google for the business; return its URL if found, else None.
    Silently skips all future calls after a rate-limit response.
    """
    global _google_rate_limited
    if _google_rate_limited:
        return None

    query = f'"{business_name}" {location_hint} official site'
    try:
        url, ok = _retry(_do_google_search, query, retries=2, backoff=3.0)
        return url if ok else None
    except PermissionError:
        console.print(
            "\n[yellow]Google has rate-limited this session. "
            "Remaining businesses will be marked as unchecked.[/yellow]"
        )
        _google_rate_limited = True
        return None
    except Exception:
        return None


# ── CSV helpers ───────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "name", "type", "distance_m", "phone", "email",
    "address", "opening_hours", "lat", "lon",
    "google_website_found", "osm_id", "osm_type",
]


def save_csv(records, filename):
    """Write records to CSV. Returns True on success, False on failure."""
    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        return True
    except PermissionError:
        console.print(
            f"[red]Permission denied writing '{filename}'. "
            "Is the file already open?[/red]"
        )
    except OSError as exc:
        console.print(f"[red]Could not write CSV: {exc}[/red]")
    return False


def _safe_filename(location_str):
    safe = re.sub(r"[^\w\-]", "_", location_str)[:30].strip("_")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"no_website_{safe}_{ts}.csv"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold green]Business No-Website Finder[/bold green]")
    console.print(
        "Finds businesses near you that [bold]don't have a website[/bold].\n"
        "[dim]Powered by OpenStreetMap — free, no API key required.[/dim]\n"
    )

    # ── Inputs ────────────────────────────────────────────────────────────────
    location_str = _prompt_nonempty(
        "[bold]Enter location (city, address, or zip code): [/bold]"
    )
    radius_km = _prompt_float(
        f"[bold]Search radius in km ({MIN_RADIUS_KM}–{MAX_RADIUS_KM}, Enter = 2.0): [/bold]",
        lo=MIN_RADIUS_KM, hi=MAX_RADIUS_KM, default=2.0,
    )
    do_google = _prompt_yes_no(
        "[bold]Google-search each business to verify no website exists? (y/n, slower): [/bold]"
    )

    radius_m = int(radius_km * 1000)
    console.print()

    # ── Geocode ───────────────────────────────────────────────────────────────
    coords = geocode_location(location_str)
    if coords is None:
        sys.exit(1)

    center_lat, center_lon = coords
    console.print(f"[green]Coordinates: {center_lat:.5f}, {center_lon:.5f}[/green]\n")

    # ── Fetch businesses ──────────────────────────────────────────────────────
    time.sleep(REQUEST_DELAY)
    elements = fetch_businesses(center_lat, center_lon, radius_m)

    if not elements:
        console.print(
            "[yellow]No businesses found. Try a larger radius, "
            "different location, or run again later.[/yellow]"
        )
        sys.exit(0)

    console.print(f"[green]{len(elements):,} raw OSM elements returned.[/green]")

    # ── Parse & deduplicate ───────────────────────────────────────────────────
    businesses = []
    seen = set()
    skipped = 0

    for el in elements:
        try:
            biz = parse_business(el, center_lat, center_lon)
        except Exception as exc:
            skipped += 1
            console.print(f"[dim red]Skipped malformed element: {exc}[/dim red]")
            continue

        if biz is None:
            continue

        key = biz["name"].lower()
        if key not in seen:
            businesses.append(biz)
            seen.add(key)

    if skipped:
        console.print(f"[yellow]{skipped} element(s) skipped due to bad/missing data.[/yellow]")

    console.print(f"[green]{len(businesses):,} unique named businesses parsed.[/green]")

    if not businesses:
        console.print("[yellow]No usable records after parsing. Exiting.[/yellow]")
        sys.exit(0)

    # ── Filter: no website in OSM ─────────────────────────────────────────────
    no_website = [b for b in businesses if not b["website"]]
    console.print(
        f"[yellow]{len(no_website):,} businesses have no website tag "
        f"in OpenStreetMap.[/yellow]\n"
    )

    if not no_website:
        console.print(
            "[green]Every business in this area already has a website listed. "
            "Nothing to report.[/green]"
        )
        sys.exit(0)

    # ── Google verification ───────────────────────────────────────────────────
    if do_google:
        console.print(
            "[cyan]Verifying via Google (≈1.5 s per business — being polite)…[/cyan]"
        )
        confirmed_no_site = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Checking…", total=len(no_website))

            for biz in no_website:
                if _google_rate_limited:
                    biz["google_website_found"] = "skipped (rate limited)"
                    confirmed_no_site.append(biz)
                    progress.advance(task)
                    continue

                progress.update(task, description=f"Checking: {biz['name'][:48]}")
                found_url = check_website_via_google(biz["name"], location_str)

                if found_url:
                    biz["google_website_found"] = found_url
                    console.print(
                        f"  [green]✓ Found for '{biz['name']}' → "
                        f"{found_url[:70]}[/green]"
                    )
                else:
                    biz["google_website_found"] = ""
                    confirmed_no_site.append(biz)

                progress.advance(task)
                time.sleep(REQUEST_DELAY)

        console.print(
            f"\n[bold red]{len(confirmed_no_site):,} businesses "
            f"confirmed to have NO website.[/bold red]"
        )
        final_list = confirmed_no_site
    else:
        for b in no_website:
            b["google_website_found"] = "not checked"
        final_list = no_website

    if not final_list:
        console.print(
            "[green]Google verified that every business here has a website. "
            "Highly saturated market![/green]"
        )
        sys.exit(0)

    # ── Sort by distance (unknown distance sorts last) ────────────────────────
    final_list.sort(
        key=lambda x: x["distance_m"] if x["distance_m"] >= 0 else float("inf")
    )

    # ── Display table ─────────────────────────────────────────────────────────
    console.print()
    table = Table(
        title=f"Businesses Without a Website — near {location_str}",
        show_lines=True,
        highlight=True,
    )
    table.add_column("#",        style="dim",        width=4,  no_wrap=True)
    table.add_column("Name",     style="bold white",  min_width=22)
    table.add_column("Type",     style="cyan",        min_width=12)
    table.add_column("Distance", style="yellow",      justify="right", width=10)
    table.add_column("Phone",    style="green",       min_width=14)
    table.add_column("Address",  style="dim",         min_width=18)

    for i, biz in enumerate(final_list, 1):
        dm = biz["distance_m"]
        if dm < 0:
            dist_str = "?"
        elif dm < 1000:
            dist_str = f"{dm:,} m"
        else:
            dist_str = f"{dm / 1000:.1f} km"

        table.add_row(
            str(i),
            biz["name"],
            biz["type"].replace("_", " ").title(),
            dist_str,
            biz["phone"] or "—",
            biz["address"] or "—",
        )

    console.print(table)

    # ── Save CSV (with automatic fallback filename) ───────────────────────────
    csv_filename = _safe_filename(location_str)
    saved = save_csv(final_list, csv_filename)

    if not saved:
        fallback = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        console.print(f"[yellow]Retrying with fallback filename: {fallback}[/yellow]")
        saved = save_csv(final_list, fallback)
        if saved:
            csv_filename = fallback

    if saved:
        console.print(
            f"\n[bold green]✓ {len(final_list):,} records saved → "
            f"[white]{csv_filename}[/white][/bold green]"
        )
    else:
        console.print(
            "[red]CSV could not be saved. Results are displayed above.[/red]"
        )

    console.print(
        "\n[dim]Tip: These businesses may be potential web design clients![/dim]"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user. Goodbye![/yellow]")
        sys.exit(0)