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

# --- Helpers ---
def normalize_street(s):
    s = s.lower().strip()
    s = re.sub(r'\b(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|court|ct)\b', '', s)
    s = re.sub(r'\b(north|south|east|west|n|s|e|w)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def load_csv(filename):
    data = []
    try:
        with open(filename, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                cleaned_row = {k.strip().lower(): v.strip() for k, v in row.items()}
                street = cleaned_row.get('street', '').lower()
                if not street:
                    continue
                club = cleaned_row.get('rotaryclub', '').upper()
                start_address = cleaned_row.get('start_address', '')
                end_address = cleaned_row.get('end_address', '')
                def safe_int(val):
                    val = val.strip()
                    return int(val) if val.isdigit() else None
                data.append({
                    "street": street,
                    "club": club,
                    "start": safe_int(start_address),
                    "end": safe_int(end_address)
                })
        print(f"✅ Loaded {len(data)} streets from {filename}", flush=True)
    except FileNotFoundError:
        print(f"⚠️ File not found: {filename}", flush=True)
    except Exception as e:
        print(f"❌ Failed to load {filename}:", e, flush=True)
    return data

def build_known_streets(data):
    known = {}
    for entry in data:
        norm = normalize_street(entry["street"])
        known[norm] = entry
    return known

def in_range(entry, house_number):
    if house_number is None:
        return True
    if entry["start"] is not None and entry["end"] is not None:
        return entry["start"] <= house_number <= entry["end"]
    return True

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

def match_address(street_data, known_streets, norm_street, house_number, street_name, formatted_address):
    # Tier 1: Exact match
    entry = known_streets.get(norm_street)
    if entry and in_range(entry, house_number):
        print(f"✅ Matched Tier 1: Exact → {entry['street']}")
        return success_payload(entry, street_name, 100, formatted_address)

    # Tier 2: Token overlap
    tokens = set(norm_street.split())
    for entry in street_data:
        entry_tokens = set(normalize_street(entry["street"]).split())
        if tokens & entry_tokens and in_range(entry, house_number):
            print(f"✅ Matched Tier 2: Token overlap → {entry['street']}")
            return success_payload(entry, street_name, 95, formatted_address)

    # Tier 3: Fuzzy match high confidence
    normalized_known = list(known_streets.keys())
    match, score = process.extractOne(norm_street, normalized_known)
    if score >= 90:
        entry = known_streets.get(match)
        if entry and in_range(entry, house_number):
            print(f"✅ Matched Tier 3: Fuzzy (≥90) → {entry['street']} ({score}%)")
            return success_payload(entry, match, score, formatted_address)

    # Tier 4: Fuzzy match + token overlap
    if score >= 80:
        match_tokens = set(match.split())
        if tokens & match_tokens:
            entry = known_streets.get(match)
            if entry and in_range(entry, house_number):
                print(f"✅ Matched Tier 4: Fuzzy (≥80) + token overlap → {entry['street']} ({score}%)")
                return success_payload(entry, match, score, formatted_address)

    return None

# Load CSV data
primary_street_data = load_csv('rotary_streets.csv')
primary_known_streets = build_known_streets(primary_street_data)

secondary_street_data = load_csv('rotary_streets_extra.csv')
secondary_known_streets = build_known_streets(secondary_street_data)

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

    # Try primary dataset first
    result = match_address(primary_street_data, primary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result:
        print("📤 Returning JSON from primary dataset:", result)
        return jsonify(result)

    # Fallback to secondary dataset if no primary match
    result = match_address(secondary_street_data, secondary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result:
        print("📤 Returning JSON from secondary dataset:", result)
        return jsonify(result)

    # No match
    print("❌ No match found in any dataset")
    combined_keys = list(primary_known_streets.keys()) + list(secondary_known_streets.keys())
    combined_match, combined_score = process.extractOne(norm_street, combined_keys)
    result = no_match_payload(norm_street, combined_match, combined_score)
    print("📤 Returning JSON:", result)
    return jsonify(result)

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running with tiered matching, dual CSV fallback, and range enforcement."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
