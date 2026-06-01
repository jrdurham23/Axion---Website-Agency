import requests
from bs4 import BeautifulSoup
import re
import datetime
import sqlite3
import csv
import time
import os
import urllib3
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.console import Group


# Suppress insecure request warnings for bad SSL certs on outdated sites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from blocklist import FRANCHISE_BLOCKLIST

def is_franchise(business_name: str) -> bool:
    name_lower = business_name.lower()
    return any(term in name_lower for term in FRANCHISE_BLOCKLIST)


def robust_request(url, params=None, headers=None, max_retries=3, **kwargs):
    import time
    import requests
    for attempt in range(max_retries):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=15, **kwargs)
            if res.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            return res
        except Exception as e:
            time.sleep(2)
    return None

def analyze_website(url):
    """
    Analyzes a website to determine if it is outdated.
    Returns (website_status, outdated_signals)
    website_status can be 'none', 'outdated', 'modern'
    """
    signals = []
    
    try:
        # Some websites block requests without a proper User-Agent
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        # Many old sites don't have valid SSL certificates
        res = robust_request(url, headers=headers, verify=False)
        if not res:
            return 'error', ['connection failed']
        
        if res.status_code >= 400:
            return "error", [f"HTTP error {res.status_code}"]
            
        html = res.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Check for mobile responsiveness
        viewport = soup.find('meta', attrs={'name': re.compile(r'viewport', re.I)})
        if not viewport:
            signals.append("no mobile responsiveness")
            
        # 2. Check for Flash elements
        if 'x-shockwave-flash' in html.lower() or '.swf' in html.lower() or soup.find('object', type="application/x-shockwave-flash"):
            signals.append("Flash elements")
            
        # 3. Check for table-based layouts (removed because modern sites often use tables for pricing/data)
        # We rely on other signals instead.
            
        # 4. Check copyright year
        # Look for things like "Copyright 2004", "© 2008", etc.
        years = re.findall(r'(?:©|Copyright|&copy;)[^\d<>\n]{0,30}(\d{4})', html, re.IGNORECASE)
        if years:
            # Filter out unrealistic years
            current_year = datetime.datetime.now().year
            valid_years = [int(y) for y in years if 1990 <= int(y) <= current_year]
            if valid_years:
                max_year = max(valid_years)
                if max_year < 2012:
                    signals.append(f"copyright year {max_year}")
                    
        # 5. Check fixed 800px resolution in css/html
        if 'width="800"' in html or 'width: 800px' in html.lower() or 'width:800px' in html.lower():
            signals.append("fixed 800px width found")

        if signals:
            return "outdated", signals
        else:
            return "modern", []

    except requests.exceptions.RequestException as e:
        return "error", ["connection failed"]
    except Exception as e:
        return "error", [f"error: {str(e)}"]

def secondary_website_check(business_name: str) -> bool:
    query = f"{business_name} official website"
    url = "https://html.duckduckgo.com/html/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    data = {'q': query}
    try:
        time.sleep(1)
        res = requests.post(url, data=data, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        results = soup.find_all('a', class_='result__url', limit=5)
        
        directories = ['yelp.', 'facebook.', 'yellowpages.', 'bbb.org', 'google.', 'mapquest.', 'tripadvisor.', 'instagram.', 'linkedin.', 'twitter.', 'x.com', 'chamberofcommerce.', 'manta.', 'angi.', 'homeadvisor.']
        
        for r in results:
            href = r.get('href', '').lower()
            if not href:
                continue
            
            is_dir = any(d in href for d in directories)
            if not is_dir:
                return True
    except Exception as e:
        pass
    return False

def get_state_bbox_from_zip(api_key, zip_code):
    url = "https://api.geoapify.com/v1/geocode/search"
    res = requests.get(url, params={'text': zip_code, 'limit': 1, 'apiKey': api_key}).json()
    if 'features' not in res or not res['features']:
        return None
    state_name = res['features'][0]['properties'].get('state')
    if not state_name:
        return None
    
    res = requests.get(url, params={'state': state_name, 'country': 'US', 'type': 'state', 'limit': 1, 'apiKey': api_key}).json()
    if 'features' not in res or not res['features']:
        return None
        
    bbox = res['features'][0].get('bbox') # [lon1, lat1, lon2, lat2]
    return state_name, bbox

def get_counties_in_bbox(bbox):
    import json
    from shapely.geometry import shape, box
    from shapely.strtree import STRtree
    
    if not os.path.exists("counties.json"):
        r = requests.get("https://eric.clst.org/assets/wiki/uploads/Stuff/gz_2010_us_050_00_20m.json")
        with open("counties.json", "w", encoding="utf-8") as f:
            f.write(r.text)
            
    with open("counties.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    geometries = []
    county_names = []
    
    for feature in data['features']:
        try:
            geom = shape(feature['geometry'])
            geometries.append(geom)
            county_names.append(feature['properties']['NAME'])
        except Exception:
            pass
            
    tree = STRtree(geometries)
    minx, miny, maxx, maxy = bbox
    state_box = box(minx, miny, maxx, maxy)
    
    intersecting_indices = tree.query(state_box)
    
    counties = []
    for idx in intersecting_indices:
        geom = geometries[idx]
        name = county_names[idx]
        c_minx, c_miny, c_maxx, c_maxy = geom.bounds
        counties.append({
            'name': name,
            'bbox': [c_minx, c_miny, c_maxx, c_maxy]
        })
    return counties

def setup_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT,
            category TEXT,
            address TEXT,
            phone TEXT,
            website_url TEXT,
            website_status TEXT,
            outdated_signals TEXT,
            source TEXT,
            date_found TEXT,
            UNIQUE(business_name, address)
        )
    ''')
    conn.commit()
    return conn

def fetch_from_geoapify(api_key, filter_str=None):
    url = "https://api.geoapify.com/v2/places"
    results = []
    
    params = {
        'categories': 'commercial,catering,service',
        'limit': 500,
        'apiKey': api_key
    }
    
    if filter_str:
        params['filter'] = filter_str
    
    try:
        res = robust_request(url, params=params)
        if not res:
            return []
        response = res.json()
        if 'features' in response:
            for feature in response['features']:
                props = feature.get('properties', {})
                results.append({
                    'name': props.get('name', props.get('address_line1', 'Unknown')),
                    'address': props.get('formatted', 'Unknown'),
                    'phone': props.get('contact', {}).get('phone', ''),
                    'website': props.get('website', None),
                    'raw_categories': props.get('categories', [])
                })
    except Exception as e:
        pass
            
    return results

def generate_leads(zip_code, categories, api_key, limit=50, db_path='leads.db', csv_path='leads.csv'):
    categories_list = [c.strip() for c in categories.split(',')]
    conn = setup_db(db_path)
    cursor = conn.cursor()
    
    all_leads = []
    seen_names = set()
    franchises_filtered = 0
    duplicates_filtered = 0
    
    console = Console()
    console.print(f"\n[bold green][+] Determining state bounds for ZIP: {zip_code}...[/bold green]")
    state_data = get_state_bbox_from_zip(api_key, zip_code)
    if not state_data:
        console.print("[red]Could not determine state bounds.[/red]")
        return
    state_name, bbox = state_data
    console.print(f"[cyan]  State: {state_name}, Bounds: {bbox}[/cyan]")
    
    console.print("[bold green][+] Building R-Tree of US Counties and querying...[/bold green]")
    counties = get_counties_in_bbox(bbox)
    console.print(f"[cyan]  Found {len(counties)} counties intersecting the state bounds.[/cyan]")

    # Setup CSV file for live appending
    file_exists = os.path.isfile(csv_path)
    csv_file = open(csv_path, 'a', newline='', encoding='utf-8')
    fieldnames = ['business_name', 'category', 'address', 'phone', 'website_url', 'website_status', 'outdated_signals', 'source', 'date_found']
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()

    table = Table(title=f"Leads Found in {zip_code}", show_lines=True)
    table.add_column("Business Name", style="cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Signals", style="yellow")
    table.add_column("Phone")

    franchises_filtered_count = [0]
    duplicates_filtered_count = [0]
    
    db_lock = Lock()
    seen_lock = Lock()
    
    def process_county(county):
        c_name = county['name']
        c_bbox = county['bbox']
        filter_str = f"rect:{c_bbox[0]},{c_bbox[1]},{c_bbox[2]},{c_bbox[3]}"
        
        # Batching: We fetch ALL relevant places in this county in a single API call
        places = fetch_from_geoapify(api_key, filter_str)
        
        for place in places:
            name = place.get('name', 'Unknown')
            name_lower = name.lower()
            raw_cats = str(place.get('raw_categories', [])).lower()
            
            # Map to user's requested categories
            matched_category = None
            for tc in categories_list:
                if tc.lower() in name_lower or tc.lower() in raw_cats:
                    matched_category = tc
                    break
                    
            if not matched_category:
                continue
                
            address = place.get('address', 'Unknown')
            phone = place.get('phone', '')
            website = place.get('website', None)
            
            if is_franchise(name):
                with seen_lock:
                    franchises_filtered_count[0] += 1
                continue
            
            with seen_lock:
                if name_lower in seen_names:
                    duplicates_filtered_count[0] += 1
                    continue
                seen_names.add(name_lower)
            
            if website:
                status, signals = analyze_website(website)
            else:
                status = "none"
                signals = ["No website found"]
            
            if status in ["none", "outdated"]:
                if secondary_website_check(name):
                    status = "modern"
                    signals = ["Found official site via secondary check"]
                
            if status in ["none", "outdated"]:
                lead = {
                    'business_name': name,
                    'category': matched_category,
                    'address': address,
                    'phone': phone,
                    'website_url': website,
                    'website_status': status,
                    'outdated_signals': ", ".join(signals),
                    'source': 'Geoapify API (Batched)',
                    'date_found': datetime.datetime.now().isoformat()
                }
                
                with db_lock:
                    all_leads.append(lead)
                    try:
                        cursor.execute('''
                            INSERT INTO leads 
                            (business_name, category, address, phone, website_url, website_status, outdated_signals, source, date_found)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            lead['business_name'], lead['category'], lead['address'], lead['phone'],
                            lead['website_url'], lead['website_status'], lead['outdated_signals'],
                            lead['source'], lead['date_found']
                        ))
                        conn.commit()
                        writer.writerow(lead)
                        csv_file.flush()
                        table.add_row(
                            lead['business_name'], 
                            lead['category'], 
                            lead['website_status'], 
                            lead['outdated_signals'], 
                            lead['phone']
                        )
                    except sqlite3.IntegrityError:
                        pass

    tasks = []
    for county in counties:
        tasks.append(county)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn()
    )
    task_id = progress.add_task("[cyan]Scanning counties (Batched API calls)...", total=len(tasks))

    group = Group(
        progress,
        table
    )

    with Live(group, console=console, refresh_per_second=4) as live:
        # Increased to 25 workers to maximize your bandwidth. Rate limits will be caught by our backoff logic.
        with ThreadPoolExecutor(max_workers=25) as executor:
            futures = [executor.submit(process_county, t) for t in tasks]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    pass
                progress.advance(task_id)

    csv_file.close()
    conn.close()
    
    if all_leads:
        console.print(f"\n[bold green][+] Done! Found {len(all_leads)} leads and saved them to {csv_path} and DB.[/bold green]")
    else:
        console.print("\n[bold yellow][-] No qualified leads found in this run.[/bold yellow]")
        
    console.print(f"[bold cyan][+] Filtered: {franchises_filtered_count[0]} franchises, {duplicates_filtered_count[0]} duplicates. Net leads: {len(all_leads)}[/bold cyan]")

