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
ABBR_MAP = {
    "st": "street",    "street": "street",
    "rd": "road",
    "road": "road",
    "ave": "avenue",
    "avenue": "avenue",
    "blvd": "boulevard",
    "boulevard": "boulevard",
    "dr": "drive",
    "drive": "drive",
    "ln": "lane",
    "lane": "lane",
    "ct": "court",
    "court": "court",
    "pl": "place",
    "place": "place",
    "sq": "square",
    "square": "square",
    "trl": "trail",
    "trail": "trail",
    "pkwy": "parkway",
    "parkway": "parkway",
    "cir": "circle",
    "circle": "circle",
    "ter": "terrace",
    "terrace": "terrace",
    "hwy": "highway",
    "highway": "highway",
    "way": "way",
    "loop": "loop",
    "cv": "cove",
    "cove": "cove",
    "expy": "expressway",
    "expressway": "expressway",
    "aly": "alley",
    "alley": "alley"
}


def normalize_street(s):
    s = s.lower().strip()
    s = re.sub(r'[^\w\s]', '', s)
    tokens = s.split()
    tokens = [ABBR_MAP.get(t, t) for t in tokens]
    return ' '.join(tokens)


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
                data.append({
                    "street": street,
                    "club": club,
                    "start": int(start_address) if start_address.isdigit() else None,
                    "end": int(end_address) if end_address.isdigit() else None
                })
        print(f"Loaded {len(data)} streets from {filename}", flush=True)
    except FileNotFoundError:
        print(f"File not found: {filename}", flush=True)
    except Exception as e:
        print(f"Failed to load {filename}:", e, flush=True)
    return data


def build_known_streets(data):
    known = {}
    for entry in data:
        norm = normalize_street(entry["street"])
        if norm not in known:
            known[norm] = []
        known[norm].append(entry)
    return known


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
    entries = known_streets.get(norm_street, [])
    if entries:
        # Step 1: Check all ranged entries first
        for entry in entries:
            start, end = entry["start"], entry["end"]
            if start is not None and end is not None and house_number is not None:
                if start <= house_number <= end:
                    print(f"Range match → {entry['street']} ({entry['club']})")
                    return success_payload(entry, street_name, 100, formatted_address)

        # Step 2: Then check any non-ranged (catch-all) entries
        for entry in entries:
            if entry["start"] is None or entry["end"] is None:
                print(f"Street match (no range) → {entry['street']} ({entry['club']})")
                return success_payload(entry, street_name, 100, formatted_address)

        # Step 3: If none of the entries matched, return not serviced
        print(f"Street '{street_name}' found but number '{house_number}' not in any serviced range.")
        return {"serviced": False,
                "reason": f"Street '{street_name.title()}' is recognized, but your address number is outside the serviced ranges."}

    # --- If no entries for this street, continue with fallback logic ---
    tokens = set(norm_street.split())
    for entry in street_data:
        entry_tokens = set(normalize_street(entry["street"]).split())
        if tokens & entry_tokens:
            print(f"Token overlap → {entry['street']}")
            return success_payload(entry, street_name, 95, formatted_address)

    normalized_known = list(known_streets.keys())
    match, score = process.extractOne(norm_street, normalized_known)
    if score >= 90:
        for entry in known_streets.get(match, []):
            print(f"Fuzzy (≥90) → {entry['street']} ({score}%)")
            return success_payload(entry, match, score, formatted_address)

    if score >= 80:
        match_tokens = set(match.split())
        if tokens & match_tokens:
            for entry in known_streets.get(match, []):
                print(f"Fuzzy (≥80) + token overlap → {entry['street']} ({score}%)")
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
    print(f"Incoming token: {repr(token)}")

    if token != AUTH_TOKEN:
        print("Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    print(f"User address input: {user_address}")

    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": user_address, "key": GOOGLE_API_KEY}

    try:
        response = requests.get(geocode_url, params=params)
        response.raise_for_status()
        data = response.json()
        print("Raw Google response:", data, flush=True)
    except Exception as e:
        print("Google API request failed:", e)
        return jsonify({"serviced": False, "reason": "Google API error"})

    if not data.get("results"):
        result = {"serviced": False, "reason": "Address not found"}
        print("Returning JSON:", result)
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

    print(f"Extracted: street='{street_name}', city='{city}', state='{state}', number='{house_number}'")

    if not street_name:
        result = {"serviced": False, "reason": "Could not extract street name"}
        print("Returning JSON:", result)
        return jsonify(result)

    if city != "wichita falls" or state != "texas":
        result = {"serviced": False, "reason": "We only service Wichita Falls, TX"}
        print("Returning JSON:", result)
        return jsonify(result)

    norm_street = normalize_street(street_name)

    # Try primary dataset first
    result = match_address(primary_street_data, primary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result:
        print("Returning JSON from primary dataset:", result)
        return jsonify(result)

    # Fallback to secondary dataset if no primary match
    result = match_address(secondary_street_data, secondary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result:
        print("Returning JSON from secondary dataset:", result)
        return jsonify(result)

    # No match
    print("No match found in any dataset")
    combined_keys = list(primary_known_streets.keys()) + list(secondary_known_streets.keys())
    combined_match, combined_score = process.extractOne(norm_street, combined_keys)
    result = no_match_payload(norm_street, combined_match, combined_score)
    print("Returning JSON:", result)
    return jsonify(result)


@app.route('/')
def home():
    return "Rotary Club Lookup API is running with tiered matching and dual CSV fallback."


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # app.run(host='0.0.0.0', port=port)
