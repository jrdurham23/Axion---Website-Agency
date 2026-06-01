import requests
from bs4 import BeautifulSoup
import re
import datetime
import sqlite3
import csv
import time
import os
import urllib3
from rich.console import Console
from rich.table import Table
from rich.live import Live


# Suppress insecure request warnings for bad SSL certs on outdated sites
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from blocklist import FRANCHISE_BLOCKLIST

def is_franchise(business_name: str) -> bool:
    name_lower = business_name.lower()
    return any(term in name_lower for term in FRANCHISE_BLOCKLIST)

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
        res = requests.get(url, timeout=10, headers=headers, verify=False)
        
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
        res = requests.post(url, data=data, headers=headers, timeout=5)
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

def get_coordinates(api_key, region):
    url = "https://api.geoapify.com/v1/geocode/search"
    params = {'text': region, 'limit': 1, 'apiKey': api_key}
    try:
        res = requests.get(url, params=params).json()
        if 'features' in res and len(res['features']) > 0:
            props = res['features'][0]['properties']
            return props.get('lon'), props.get('lat')
    except Exception as e:
        print(f"  Error getting coordinates for {region}: {e}")
    return None, None

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

def fetch_from_geoapify(api_key, query, max_results, lon=None, lat=None):
    url = "https://api.geoapify.com/v1/geocode/search"
    results = []
    
    params = {
        'text': query,
        'limit': min(max_results, 50),
        'apiKey': api_key
    }
    
    if lon and lat:
        # Filter within a 50km (~31 miles) radius
        params['filter'] = f"circle:{lon},{lat},50000"
        params['bias'] = f"proximity:{lon},{lat}"
    
    try:
        response = requests.get(url, params=params).json()
        if 'features' in response:
            for feature in response['features']:
                props = feature.get('properties', {})
                results.append({
                    'name': props.get('name', props.get('address_line1', 'Unknown')),
                    'address': props.get('formatted', 'Unknown'),
                    'phone': props.get('contact', {}).get('phone', ''),
                    'website': props.get('website', None)
                })
    except Exception as e:
        print(f"  Error fetching from Geoapify: {e}")
            
    return results[:max_results]

def generate_leads(region, categories, api_key, limit=50, db_path='leads.db', csv_path='leads.csv'):
    categories_list = [c.strip() for c in categories.split(',')]
    conn = setup_db(db_path)
    cursor = conn.cursor()
    
    all_leads = []
    seen_names = set()
    franchises_filtered = 0
    duplicates_filtered = 0
    
    console = Console()
    console.print(f"\n[bold green][+] Geocoding region: {region}...[/bold green]")
    lon, lat = get_coordinates(api_key, region)
    if lon and lat:
        console.print(f"[cyan]  Coordinates found: {lat}, {lon}. Distance filter applied (50km).[/cyan]")
    else:
        console.print(f"[yellow]  Could not get coordinates for region. Distance filter will not be applied.[/yellow]")

    # Setup CSV file for live appending
    file_exists = os.path.isfile(csv_path)
    csv_file = open(csv_path, 'a', newline='', encoding='utf-8')
    fieldnames = ['business_name', 'category', 'address', 'phone', 'website_url', 'website_status', 'outdated_signals', 'source', 'date_found']
    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()

    table = Table(title=f"Leads Found in {region}", show_lines=True)
    table.add_column("Business Name", style="cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Signals", style="yellow")
    table.add_column("Phone")

    with Live(table, console=console, refresh_per_second=4) as live:


        for category in categories_list:
            query = f"{category} in {region}"
            places = fetch_from_geoapify(api_key, query, limit, lon, lat)
            
            for place in places:
                name = place.get('name', 'Unknown')
                address = place.get('address', 'Unknown')
                phone = place.get('phone', '')
                website = place.get('website', None)
                
                if is_franchise(name):
                    franchises_filtered += 1
                    continue
                
                name_lower = name.lower()
                if name_lower in seen_names:
                    duplicates_filtered += 1
                    continue
                seen_names.add(name_lower)
                
                if website:
                    status, signals = analyze_website(website)
                else:
                    status = "none"
                    signals = ["No website found"]
                
                # Secondary check if they don't have a modern site yet
                if status in ["none", "outdated"]:
                    if secondary_website_check(name):
                        status = "modern"
                        signals = ["Found official site via secondary check"]
                    
                if status in ["none", "outdated"]:
                    lead = {
                        'business_name': name,
                        'category': category,
                        'address': address,
                        'phone': phone,
                        'website_url': website,
                        'website_status': status,
                        'outdated_signals': ", ".join(signals),
                        'source': 'Geoapify API',
                        'date_found': datetime.datetime.now().isoformat()
                    }
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
                        
                        # Live update the CSV
                        writer.writerow(lead)
                        csv_file.flush()
                        
                        # Live update the CLI table
                        table.add_row(
                            lead['business_name'], 
                            lead['category'], 
                            lead['website_status'], 
                            lead['outdated_signals'], 
                            lead['phone']
                        )
                    except sqlite3.IntegrityError:
                        pass # Duplicate

    csv_file.close()
    conn.close()
    
    if all_leads:
        console.print(f"\n[bold green][+] Done! Found {len(all_leads)} leads and saved them to {csv_path} and DB.[/bold green]")
    else:
        console.print("\n[bold yellow][-] No qualified leads found in this run.[/bold yellow]")
        
    console.print(f"[bold cyan][+] Filtered: {franchises_filtered} franchises, {duplicates_filtered} duplicates. Net leads: {len(all_leads)}[/bold cyan]")

