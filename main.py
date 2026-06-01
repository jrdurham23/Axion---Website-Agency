import sys
import argparse
import os
from dotenv import load_dotenv
from lead_generator import generate_leads

def main():
    load_dotenv()
    api_key = os.getenv("GEOAPIFY_API_KEY")
    
    if not api_key:
        print("\n[-] Error: GEOAPIFY_API_KEY is not set.")
        print("[-] Please create a .env file based on .env.example and add your Geoapify API key.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Regional Business Lead Generation Database")
    parser.add_argument("--zip", help="The target ZIP code (e.g., '31401')")
    parser.add_argument("--categories", default="HVAC, auto repair, landscaping, restaurants, retail, plumbing, electrician, roofing", help="Comma-separated list of business types")
    parser.add_argument("--limit", type=int, default=50, help="Max results to fetch per category per county")
    parser.add_argument("--db", default="leads.db", help="SQLite database filename")
    parser.add_argument("--csv", default="leads.csv", help="CSV export filename")
    
    args = parser.parse_args()
    
    if args.zip:
        zip_code = args.zip
        categories = args.categories
        limit = args.limit
        db_path = args.db
        csv_path = args.csv
    else:
        print("=" * 60)
        print("   Regional Business Lead Generation Database")
        print("=" * 60)
        print("\nWelcome! This tool will help you find local businesses with missing or outdated websites.\n")
        
        zip_code = input("1. What ZIP code would you like to search? (e.g. '31401'): ").strip()
        while not zip_code:
            print("   [-] ZIP code cannot be empty.")
            zip_code = input("   What ZIP code would you like to search?: ").strip()
            
        categories = "HVAC, auto repair, landscaping, restaurants, retail, plumbing, electrician, roofing"
        print(f"2. Business categories to search: {categories}")
            
        limit_str = input("3. What is the max number of places to fetch per category per county? (default 50): ").strip()
        limit = int(limit_str) if limit_str.isdigit() else 50
            
        db_path = input("4. SQLite Database filename (default 'leads.db'): ").strip() or 'leads.db'
        csv_path = input("5. CSV Export filename (default 'leads.csv'): ").strip() or 'leads.csv'

    print("\n" + "=" * 60)
    print(f"Starting generation process for ZIP {zip_code}. This may take a while...")
    print("=" * 60)
    
    try:
        generate_leads(
            zip_code=zip_code,
            categories=categories,
            api_key=api_key,
            limit=limit,
            db_path=db_path,
            csv_path=csv_path
        )
    except KeyboardInterrupt:
        print("\n\n[-] Process interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[-] An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
