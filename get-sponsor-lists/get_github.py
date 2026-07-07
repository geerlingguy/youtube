# Sponsorship data loader for GitHub Sponsors.
#
# Requires environment variables:
#   - GITHUB_SPONSORS_TOKEN (https://github.com/settings/tokens)
#   - (Classic token needs read:org and read:user scope)

import os
import sys
import csv
import requests

# 1. Configuration
ENV_VAR_NAME = "GITHUB_SPONSORS_TOKEN"
GITHUB_TOKEN = os.getenv(ENV_VAR_NAME)
GITHUB_ACCOUNT = "geerlingguy"
IS_ORGANIZATION = False
OUTPUT_FILE = "github_active_sponsors.csv"

# Validate Token presence
if not GITHUB_TOKEN:
    print(f"Error: Please set the {ENV_VAR_NAME} environment variable.")
    print(f"Run: export {ENV_VAR_NAME}=\"your_github_pat_here\"")
    sys.exit(1)

# 2. GraphQL Query Definition
query = """
query($login: String!, $cursor: String) {
  account: %s(login: $login) {
    sponsorshipsAsMaintainer(first: 100, after: $cursor, includePrivate: true) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        sponsorEntity {
          ... on User {
            login
            name
          }
          ... on Organization {
            login
            name
          }
        }
        tier {
          monthlyPriceInCents
          monthlyPriceInDollars
        }
        isOneTimePayment
      }
    }
  }
}
""" % ("organization" if IS_ORGANIZATION else "user")

url = "https://api.github.com/graphql"
# Using standard Token or Bearer string formatting
headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

active_sponsors = []
has_next_page = True
cursor = None

print(f"Fetching GitHub Sponsors data for @{GITHUB_ACCOUNT}...")

# 3. Pagination Loop
while has_next_page:
    variables = {"login": GITHUB_ACCOUNT, "cursor": cursor}
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)

    if response.status_code != 200:
        print(f"\nError: API request failed with status code {response.status_code}")
        if "text/html" in response.headers.get("Content-Type", ""):
            print("Received HTML instead of JSON. This usually means your Token is invalid, expired, or lacks scopes.")
        else:
            print(response.text)
        sys.exit(1)

    res_data = response.json()

    if "errors" in res_data:
        print("\nGraphQL Errors returned:")
        for error in res_data["errors"]:
            print(f"- {error['message']}")
        sys.exit(1)

    account_data = res_data.get("data", {}).get("account")
    if not account_data:
        print(f"\nError: Could not find account profile data for '{GITHUB_ACCOUNT}'. check IS_ORGANIZATION setting.")
        sys.exit(1)

    sponsorships_data = account_data["sponsorshipsAsMaintainer"]
    nodes = sponsorships_data["nodes"]

    for node in nodes:
        sponsor = node["sponsorEntity"]
        tier = node["tier"]

        # Guard against empty/deleted accounts or custom zero tiers
        if not sponsor:
            continue

        cents = tier["monthlyPriceInCents"] if tier else 0
        dollars = tier["monthlyPriceInDollars"] if tier else 0

        display_name = sponsor.get("name") or sponsor.get("login")
        username = sponsor.get("login")
        payment_type = "One-Time" if node["isOneTimePayment"] else "Recurring"

        active_sponsors.append({
            "Name": display_name,
            "Username": username,
            "Type": payment_type,
            "Amount (USD)": dollars,
            "amount_cents": cents  # Kept hidden just for sorting sorting logic
        })

    page_info = sponsorships_data["pageInfo"]
    has_next_page = page_info["hasNextPage"]
    cursor = page_info["endCursor"]

# 4. Sorting logic (Highest funding amount first)
active_sponsors.sort(key=lambda x: x["amount_cents"], reverse=True)

# 5. Export directly to CSV file
csv_columns = ["Name", "Username", "Type", "Amount (USD)"]

try:
    with open(OUTPUT_FILE, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=csv_columns)
        writer.writeheader()
        for sponsor in active_sponsors:
            # Remove helper key before writing row
            row = {col: sponsor[col] for col in csv_columns}
            writer.writerow(row)

    print(f"\nSuccess! Found {len(active_sponsors)} active sponsors.")
    print(f"Data successfully saved to: {OUTPUT_FILE}")

except IOError as e:
    print(f"Error writing to CSV file: {e}")
