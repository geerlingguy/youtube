# Sponsorship data loader for Patreon Sponsors.
#
# Requires environment variables:
#   - PATREON_ACCESS_TOKEN (https://www.patreon.com/portal/registration/register-clients)
#   - PATREON_CAMPAIGN_ID (https://github.com/AceAsin/PatreonBadge)

import csv
import os
import requests
from urllib.parse import urlparse, urlunparse

# --- CONFIGURATION VALIDATION ---
if 'PATREON_ACCESS_TOKEN' not in os.environ:
    raise KeyError("Missing environment variable: Please set 'PATREON_ACCESS_TOKEN' before running this script.")
if 'PATREON_CAMPAIGN_ID' not in os.environ:
    raise KeyError("Missing environment variable: Please set 'PATREON_CAMPAIGN_ID' before running this script.")

ACCESS_TOKEN = os.environ['PATREON_ACCESS_TOKEN']
CAMPAIGN_ID = os.environ['PATREON_CAMPAIGN_ID']

BASE_URL = "https://patreon.com"
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "Patreon-Fetcher/13.0"
}

def clean_next_url(url_string):
    """Safely injects the API v2 prefix into the path if Patreon omits it."""
    parsed = urlparse(url_string)
    path = parsed.path
    if not path.startswith("/api/oauth2/v2"):
        clean_path = path.lstrip("/")
        new_path = f"/api/oauth2/v2/{clean_path}"
        parsed = parsed._replace(path=new_path)
    return urlunparse(parsed)

def fetch_all_active_patrons(campaign_id):
    """Fetches all active members and includes their tier info using explicit v2 formatting."""
    url = f"{BASE_URL}/campaigns/{campaign_id}/members"

    params = {
        "page[count]": 50,
        "fields[member]": "patron_status,full_name,email,currently_entitled_amount_cents",
        "fields[tier]": "title,amount_cents",
        "include": "currently_entitled_tiers"
    }

    active_patrons = []
    page_count = 0

    while url:
        page_count += 1
        print(f"Processing page {page_count} of patron records...")
        url = clean_next_url(url)

        response = requests.get(url, headers=HEADERS, params=params)

        if response.status_code != 200:
            print(f"\n[Error] API Request Failed! Status Code: {response.status_code}")
            print(f"[Attempted URL]: {url}")
            response.raise_for_status()

        payload = response.json()

        tier_lookup = {}
        if 'included' in payload:
            for item in payload['included']:
                if item['type'] == 'tier':
                    tier_lookup[item['id']] = {
                        'title': item['attributes'].get('title', 'Custom Amount'),
                        'amount_cents': item['attributes'].get('amount_cents', 0)
                    }

        for member in payload.get('data', []):
            attrs = member.get('attributes', {})
            highest_tier_amount = attrs.get('currently_entitled_amount_cents', 0)
            if highest_tier_amount <= 0:
                continue

            highest_tier_title = "No Tier"
            base_tier_cost_cents = 0
            relationships = member.get('relationships', {})
            tier_data = relationships.get('currently_entitled_tiers', {}).get('data', [])

            temp_max = -1
            for t in tier_data:
                t_id = t['id']
                if t_id in tier_lookup:
                    tier_info = tier_lookup[t_id]
                    if tier_info['amount_cents'] > temp_max:
                        temp_max = tier_info['amount_cents']
                        highest_tier_title = tier_info['title']
                        base_tier_cost_cents = tier_info['amount_cents']

            # If they didn't pick an official tier but pay money, flag as Custom Pledge
            is_custom = False
            if highest_tier_title == "No Tier" and highest_tier_amount > 0:
                highest_tier_title = "Custom Pledge"
                base_tier_cost_cents = highest_tier_amount
                is_custom = True

            active_patrons.append({
                "name": attrs.get("full_name", "Unknown"),
                "email": attrs.get("email", "Hidden/Unknown"),
                "tier": highest_tier_title,
                "base_rate_usd": round(base_tier_cost_cents / 100, 2),
                "actual_amount_usd": round(highest_tier_amount / 100, 2),
                "is_custom_pledge": is_custom
            })

        links = payload.get('links', {})
        url = links.get('next')
        params = None

    return active_patrons

def sort_patrons_by_rule(patrons):
    """Sorts official tiers by base price descending, pushing Custom Pledges to the absolute bottom."""
    # Sorting key logic:
    # 1. x['is_custom_pledge'] evaluates to 0 for False and 1 for True (pushes True to the bottom)
    # 2. -x['base_rate_usd'] ranks higher baseline fees first
    patrons.sort(key=lambda x: (x['is_custom_pledge'], -x['base_rate_usd']))
    return patrons

def save_to_csv(patrons, filename="patreon_active_patrons.csv"):
    if not patrons:
        return

    keys = ['name', 'email', 'tier', 'base_rate_usd', 'actual_amount_usd']
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(patrons)
    print(f"CSV exported smoothly: {filename}")

def save_to_grouped_text(patrons, filename="patreon_active_patrons.txt"):
    if not patrons:
        print("No paid patrons found matching parameter configurations.")
        return

    current_tier = None
    with open(filename, 'w', encoding='utf-8') as f:
        for patron in patrons:
            # Set the header block label
            if patron['is_custom_pledge']:
                tier_display_name = f"=== {patron['tier']} ==="
            else:
                tier_display_name = f"=== {patron['tier']} (${patron['base_rate_usd']}/mo) ==="

            # Print the header change
            if tier_display_name != current_tier:
                current_tier = tier_display_name
                if f.tell() > 0:
                    f.write("\n")
                f.write(f"{current_tier}\n")

            # Print just the clean text name, completely hiding price strings
            f.write(f"{patron['name']}\n")

    print(f"Grouped text file exported smoothly: {filename}")

def main():
    try:
        print(f"Connecting to Patreon API v2 using Target Campaign ID: {CAMPAIGN_ID}...")
        patrons = fetch_all_active_patrons(CAMPAIGN_ID)

        # Apply the final master sorting layout to the data pool
        sorted_patrons = sort_patrons_by_rule(patrons)

        save_to_csv(sorted_patrons)
        save_to_grouped_text(sorted_patrons)
        print(f"\nFinished! Processed all {len(sorted_patrons)} active paid supporters successfully.")
    except Exception as e:
        print(f"\nAn issue occurred during execution: {e}")

if __name__ == "__main__":
    main()
