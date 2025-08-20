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

def safe_int(val):
    val = val.strip()
    return int(val) if val.isdigit() else None

def load_csv(filename):
    data = []
    try:
        with open(filename, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                cleaned = {k.strip().lower(): v.strip() for k, v in row.items()}
                street = cleaned.get('street', '').lower()
                if not street:
                    continue
                club = cleaned.get('rotaryclub', '').upper()
                start = safe_int(cleaned.get('start_address', ''))
                end = safe_int(cleaned.get('end_address', ''))
                data.append({
                    "street": street,
                    "club": club,
                    "start": start,
                    "end": end
                })
        print(f"✅ Loaded {len(data)} entries from {filename}")
    except FileNotFoundError:
        print(f"⚠️ File not found: {filename}")
    except Exception as e:
        print(f"❌ Error loading {filename}: {e}")
    return data

def build_known_streets(data):
    known = {}
    for entry in data:
        norm = normalize_street(entry["street"])
        known.setdefault(norm, []).append(entry)
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

def match_address(street_data, known_streets, norm_street, house_number, original_street_name, formatted_address):
    tokens = set(norm_street.split())

    # Tier 1: Exact match
    for entry in known_streets.get(norm_street, []):
        if in_range(entry, house_number):
            print(f"✅ Tier 1: Exact match → {entry['street']}")
            return success_payload(entry, original_street_name, 100, formatted_address)

    # Tier 2: Token overlap
    for entry in street_data:
        entry_tokens = set(normalize_street(entry["street"]).split())
        if tokens & entry_tokens and in_range(entry, house_number):
            print(f"✅ Tier 2: Token overlap → {entry['street']}")
            return success_payload(entry, original_street_name, 95, formatted_address)

    # Tier 3: Fuzzy match with score ≥ 90
    normalized_keys = list(known_streets.keys())
    match, score = process.extractOne(norm_street, normalized_keys)
    if score >= 90:
        for entry in known_streets.get(match, []):
            if in_range(entry, house_number):
                print(f"✅ Tier 3: Fuzzy ≥90 → {entry['street']} ({score}%)")
                return success_payload(entry, match, score, formatted_address)

    # Tier 4: Fuzzy ≥ 80 + token overlap
    if score >= 80:
        match_tokens = set(match.split())
        if tokens & match_tokens:
            for entry in known_streets.get(match, []):
                if in_range(entry, house_number):
                    print(f"✅ Tier 4: Fuzzy + Token overlap → {entry['street']} ({score}%)")
                    return success_payload(entry, match, score, formatted_address)

    return None

# --- Load Data ---

primary_data = load_csv('rotary_streets.csv')
secondary_data = load_csv('rotary_streets_extra.csv')

all_data = primary_data + secondary_data
known_streets = build_known_streets(all_data)

# --- Routes ---

@app.route('/check', methods=['GET'])
def check_address():
    token = request.args.get('token', '')
    if token != AUTH_TOKEN:
        print("❌ Token mismatch")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    print(f"🔍 Address input: {user_address}")

    if not user_address:
        return jsonify({"serviced": False, "reason": "No address provided"})

    # Call Google Geocoding API
    try:
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params={
            "address": user_address,
            "key": GOOGLE_API_KEY
        })
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"❌ Google API error: {e}")
        return jsonify({"serviced": False, "reason": "Google API error"})

    if not data.get("results"):
        return jsonify({"serviced": False, "reason": "Address not found"})

    components = data["results"][0].get("address_components", [])
    formatted_address = data["results"][0].get("formatted_address", "")
    street_name = city = state = ""
    house_number = None

    for comp in components:
        if "street_number" in comp["types"]:
            try:
                house_number = int(comp["long_name"])
            except:
                pass
        elif "route" in comp["types"]:
            street_name = comp["long_name"].lower().strip()
        elif "locality" in comp["types"]:
            city = comp["long_name"].lower().strip()
        elif "administrative_area_level_1" in comp["types"]:
            state = comp["long_name"].lower().strip()

    print(f"🏡 Parsed: street='{street_name}', city='{city}', state='{state}', number='{house_number}'")

    if not street_name:
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX"})

    norm_street = normalize_street(street_name)
    result = match_address(all_data, known_streets, norm_street, house_number, street_name, formatted_address)

    if result:
        return jsonify(result)

    # Fallback: suggest best fuzzy match
    all_keys = list(known_streets.keys())
    closest_match, score = process.extractOne(norm_street, all_keys)
    result = no_match_payload(norm_street, closest_match, score)
    return jsonify(result)

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
