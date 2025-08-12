from flask import Flask, request, jsonify
import csv
import requests
import os
import re
from fuzzywuzzy import process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "your-google-api-key")
print(f"🚀 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

street_data = []
known_streets = []

# --- Helpers ---
def normalize_street(s):
    s = s.lower().strip()
    s = re.sub(r'\b(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|ct)\b', '', s)
    s = re.sub(r'\b(north|south|east|west|n|s|e|w)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def load_csv_file(filename):
    temp_dict = {}
    try:
        with open(filename, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                cleaned_row = {k.strip().lower(): v.strip() for k, v in row.items()}
                street = cleaned_row.get('street', '').lower()
                club = cleaned_row.get('rotaryclub', '').upper()
                start_address = cleaned_row.get('start_address', '')
                end_address = cleaned_row.get('end_address', '')

                if street and club:
                    norm = normalize_street(street)
                    temp_dict[norm] = {
                        "street": street,
                        "club": club,
                        "start": int(start_address) if start_address.isdigit() else None,
                        "end": int(end_address) if end_address.isdigit() else None
                    }
        print(f"✅ Loaded {len(temp_dict)} streets from {filename}", flush=True)
    except FileNotFoundError:
        print(f"⚠️ File not found: {filename}", flush=True)
    except Exception as e:
        print(f"❌ Failed to load {filename}:", e, flush=True)
    return temp_dict

# Load primary then backup, overwriting earlier entries
all_data = {}
all_data.update(load_csv_file('rotary_streets.csv'))
all_data.update(load_csv_file('rotary_streets_backup.csv'))

# Convert dict to lists
street_data = list(all_data.values())
known_streets = [entry['street'] for entry in street_data]

def find_entry(norm_street):
    for entry in street_data:
        if normalize_street(entry["street"]) == norm_street:
            return entry
    return None

def success_payload(entry, matched_street, score, formatted_address):
    return {
        "serviced": True,
        "rotary_club": entry["club"],
        "matched_street": matched_street.title(),
        "confidence_score": score,
        "confirmed_address": formatted_address
    }

def no_match_payload(user_street, best_match, score):
    return {
        "serviced": False,
        "reason": f"No matching service street found for '{user_street.title()}'. Closest match: '{best_match.title()}' ({score}%)",
        "suggestions": [{"street": best_match.title(), "score": score}]
    }

@app.route('/check', methods=['GET'])
def check_address():
    token = request.args.get('token', '')
    print(f"🔐 Incoming token: {repr(token)}")

    if token != AUTH_TOKEN:
        print("❌ Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    print(f"🔍 User address input: {user_address}")

    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": user_address, "key": GOOGLE_API_KEY}

    try:
        response = requests.get(geocode_url, params=params)
        response.raise_for_status()
        data = response.json()
        print("📍 Raw Google response:", data, flush=True)
    except Exception as e:
        print("❌ Google API request failed:", e)
        return jsonify({"serviced": False, "reason": "Google API error"})

    if not data.get("results"):
        result = {"serviced": False, "reason": "Address not found"}
        print("❌ Returning JSON:", result)
        return jsonify(result)

    address_components = data["results"][0].get("address_components", [])
    formatted_address = data["results"][0].get("formatted_address", "")
    street_name = ""
    city = ""
    state = ""
    house_number = None

    for component in address_components:
        if "street_number" in component["types"]:
            try:
                house_number = int(component["long_name"])
            except:
                pass
        elif "route" in component["types"]:
            street_name = component["long_name"].lower().strip()
        elif "locality" in component["types"]:
            city = component["long_name"].lower().strip()
        elif "administrative_area_level_1" in component["types"]:
            state = component["long_name"].lower().strip()

    print(f"🚏 Extracted: street='{street_name}', city='{city}', state='{state}', number='{house_number}'")

    if not street_name:
        result = {"serviced": False, "reason": "Could not extract street name"}
        print("❌ Returning JSON:", result)
        return jsonify(result)

    if city != "wichita falls" or state != "texas":
        result = {"serviced": False, "reason": "We only service Wichita Falls, TX"}
        print("❌ Returning JSON:", result)
        return jsonify(result)

    norm_street = normalize_street(street_name)

    # Tier 1: Exact match + house range
    for entry in street_data:
        if normalize_street(entry["street"]) == norm_street:
            if entry["start"] is not None and entry["end"] is not None and house_number is not None:
                if entry["start"] <= house_number <= entry["end"]:
                    print(f"✅ Matched Tier 1: Exact street + house range → {entry['street']}")
                    result = success_payload(entry, street_name, 100, formatted_address)
                    print("📤 Returning JSON:", result)
                    return jsonify(result)
            else:
                print(f"✅ Matched Tier 1: Exact street (no house range check) → {entry['street']}")
                result = success_payload(entry, street_name, 100, formatted_address)
                print("📤 Returning JSON:", result)
                return jsonify(result)

    # Tier 2: Exact match ignoring house number
    for entry in street_data:
        if normalize_street(entry["street"]) == norm_street:
            print(f"✅ Matched Tier 2: Exact street ignoring house number → {entry['street']}")
            result = success_payload(entry, street_name, 100, formatted_address)
            print("📤 Returning JSON:", result)
            return jsonify(result)

    # Tier 3: Token overlap
    tokens = set(norm_street.split())
    for entry in street_data:
        entry_tokens = set(normalize_street(entry["street"]).split())
        if tokens & entry_tokens:
            print(f"✅ Matched Tier 3: Token overlap → {entry['street']}")
            result = success_payload(entry, street_name, 95, formatted_address)
            print("📤 Returning JSON:", result)
            return jsonify(result)

    # Tier 4: Fuzzy match high confidence
    normalized_known = [normalize_street(s) for s in known_st]()_
