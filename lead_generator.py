import requests
from bs4 import BeautifulSoup
import re
import datetime
import sqlite3
import csv
import time
import os
import urllib3
import logging
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.console import Group

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from blocklist import FRANCHISE_BLOCKLIST

class LeadGenerator:
    def __init__(self, api_key, db_path='leads.db', csv_path='leads.csv', max_workers=10):
        self.api_key = api_key
        self.db_path = db_path
        self.csv_path = csv_path
        self.max_workers = max_workers
        self.seen_names = set()
        self.franchises_filtered = 0
        self.duplicates_filtered = 0
        self.db_lock = Lock()
        self.seen_lock = Lock()
        self.setup_db()
        self.setup_csv()

    def setup_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
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
        self.conn.commit()

    def setup_csv(self):
        file_exists = os.path.isfile(self.csv_path)
        self.csv_file = open(self.csv_path, 'a', newline='', encoding='utf-8')
        self.fieldnames = ['business_name', 'category', 'address', 'phone', 'website_url', 'website_status', 'outdated_signals', 'source', 'date_found']
        self.writer = csv.DictWriter(self.csv_file, fieldnames=self.fieldnames)
        if not file_exists:
            self.writer.writeheader()

    def is_franchise(self, business_name: str) -> bool:
        name_lower = business_name.lower()
        return any(term in name_lower for term in FRANCHISE_BLOCKLIST)

    def robust_request(self, url, params=None, headers=None, max_retries=3, **kwargs):
        for attempt in range(max_retries):
            try:
                res = requests.get(url, params=params, headers=headers, timeout=15, **kwargs)
                if res.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                return res
            except Exception:
                time.sleep(2)
        return None

    def analyze_website(self, url):
        signals = []
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = self.robust_request(url, headers=headers, verify=False)
            if not res:
                return 'error', ['connection failed']
            if res.status_code >= 400:
                return "error", [f"HTTP error {res.status_code}"]
                
            html = res.text
            soup = BeautifulSoup(html, 'html.parser')
            
            if not soup.find('meta', attrs={'name': re.compile(r'viewport', re.I)}):
                signals.append("no mobile responsiveness")
            if 'x-shockwave-flash' in html.lower() or '.swf' in html.lower():
                signals.append("Flash elements")
                
            years = re.findall(r'(?:©|Copyright|&copy;)[^\d<>\n]{0,30}(\d{4})', html, re.IGNORECASE)
            if years:
                valid_years = [int(y) for y in years if 1990 <= int(y) <= datetime.datetime.now().year]
                if valid_years and max(valid_years) < 2012:
                    signals.append(f"copyright year {max(valid_years)}")
                        
            if 'width="800"' in html or 'width: 800px' in html.lower() or 'width:800px' in html.lower():
                signals.append("fixed 800px width")

            return ("outdated", signals) if signals else ("modern", [])
        except Exception as e:
            return "error", [f"error: {str(e)}"]

    def secondary_website_check(self, business_name: str) -> bool:
        url = "https://html.duckduckgo.com/html/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        data = {'q': f"{business_name} official website"}
        try:
            time.sleep(1)
            res = requests.post(url, data=data, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            results = soup.find_all('a', class_='result__url', limit=5)
            directories = ['yelp.', 'facebook.', 'yellowpages.', 'bbb.org', 'google.', 'mapquest.', 'tripadvisor.', 'instagram.', 'linkedin.', 'twitter.', 'x.com', 'chamberofcommerce.', 'manta.', 'angi.', 'homeadvisor.']
            
            for r in results:
                href = r.get('href', '').lower()
                if href and not any(d in href for d in directories):
                    return True
        except Exception:
            pass
        return False

    def get_county_from_zip(self, zip_code):
        url = "https://api.geoapify.com/v1/geocode/search"
        res = requests.get(url, params={'text': zip_code, 'limit': 1, 'apiKey': self.api_key}).json()
        if 'features' not in res or not res['features']:
            return None, None
        props = res['features'][0]['properties']
        
        county = props.get('county')
        if not county:
            # Fallback to city or other local administrative area if county is missing
            county = props.get('city') or props.get('municipality') or props.get('name')
            
        return county, props.get('state')

    def fetch_places(self, query, max_results):
        url = "https://api.geoapify.com/v1/geocode/search"
        results = []
        params = {
            'text': query,
            'limit': min(max_results, 50),
            'apiKey': self.api_key
        }
        res = self.robust_request(url, params=params)
        if not res: return []
        
        try:
            response = res.json()
            for feature in response.get('features', []):
                props = feature.get('properties', {})
                results.append({
                    'name': props.get('name', props.get('address_line1', 'Unknown')),
                    'address': props.get('formatted', 'Unknown'),
                    'phone': props.get('contact', {}).get('phone', ''),
                    'website': props.get('website', None)
                })
        except Exception:
            pass
        return results

    def process_category(self, county, state, category, limit, table, live_ctx):
        query = f"{category} in {county}, {state}"
        places = self.fetch_places(query, limit)
        local_leads = []
        
        for place in places:
            name = place.get('name', 'Unknown')
            if self.is_franchise(name):
                with self.seen_lock: self.franchises_filtered += 1
                continue
                
            name_lower = name.lower()
            with self.seen_lock:
                if name_lower in self.seen_names:
                    self.duplicates_filtered += 1
                    continue
                self.seen_names.add(name_lower)
                
            website = place.get('website')
            if website:
                status, signals = self.analyze_website(website)
            else:
                status, signals = "none", ["No website found"]
                
            if status in ["none", "outdated"] and self.secondary_website_check(name):
                status, signals = "modern", ["Found official site via DuckDuckGo"]
                
            if status in ["none", "outdated"]:
                lead = {
                    'business_name': name,
                    'category': category,
                    'address': place.get('address', 'Unknown'),
                    'phone': place.get('phone', ''),
                    'website_url': website,
                    'website_status': status,
                    'outdated_signals': ", ".join(signals),
                    'source': 'Geoapify Geocoding API',
                    'date_found': datetime.datetime.now().isoformat()
                }
                
                with self.db_lock:
                    local_leads.append(lead)
                    try:
                        self.cursor.execute('''
                            INSERT INTO leads 
                            (business_name, category, address, phone, website_url, website_status, outdated_signals, source, date_found)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (lead['business_name'], lead['category'], lead['address'], lead['phone'],
                              lead['website_url'], lead['website_status'], lead['outdated_signals'], lead['source'], lead['date_found']))
                        self.conn.commit()
                        self.writer.writerow(lead)
                        self.csv_file.flush()
                        table.add_row(lead['business_name'], lead['category'], lead['website_status'], lead['outdated_signals'], lead['phone'])
                    except sqlite3.IntegrityError:
                        pass
        return local_leads

    def close(self):
        self.csv_file.close()
        self.conn.close()

def generate_leads(zip_code, categories, api_key, limit=50, db_path='leads.db', csv_path='leads.csv'):
    categories_list = [c.strip() for c in categories.split(',')]
    generator = LeadGenerator(api_key, db_path, csv_path)
    
    console = Console()
    console.print(f"\n[bold green][+] Determining county for ZIP: {zip_code}...[/bold green]")
    county, state = generator.get_county_from_zip(zip_code)
    
    if not county or not state:
        console.print("[bold red][-] Could not determine the county for this ZIP code. Please try a different ZIP.[/bold red]")
        return
        
    console.print(f"[cyan]  Found: {county}, {state}[/cyan]")
    
    table = Table(title=f"Leads Found in {county}, {state}", show_lines=True)
    table.add_column("Business Name", style="cyan")
    table.add_column("Category", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Signals", style="yellow")
    table.add_column("Phone")
    
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn()
    )
    task_id = progress.add_task(f"[cyan]Scanning {county}...", total=len(categories_list))
    
    group = Group(progress, table)
    
    all_leads = []
    
    with Live(group, console=console, refresh_per_second=4) as live:
        with ThreadPoolExecutor(max_workers=generator.max_workers) as executor:
            futures = [executor.submit(generator.process_category, county, state, cat, limit, table, live) for cat in categories_list]
            for future in as_completed(futures):
                try:
                    leads = future.result()
                    all_leads.extend(leads)
                except Exception as e:
                    pass
                progress.advance(task_id)

    generator.close()
    
    if all_leads:
        console.print(f"\n[bold green][+] Done! Found {len(all_leads)} leads and saved them to {csv_path} and DB.[/bold green]")
    else:
        console.print("\n[bold yellow][-] No qualified leads found in this run.[/bold yellow]")
        
    console.print(f"[bold cyan][+] Filtered: {generator.franchises_filtered} franchises, {generator.duplicates_filtered} duplicates. Net leads: {len(all_leads)}[/bold cyan]")
