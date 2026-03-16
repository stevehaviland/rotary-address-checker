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
    # Street types
    "st": "street", "st.": "street", "street": "street",
    "rd": "road", "rd.": "road", "road": "road",
    "ave": "avenue", "ave.": "avenue", "avenue": "avenue",
    "blvd": "boulevard", "blvd.": "boulevard", "boulevard": "boulevard",
    "dr": "drive", "dr.": "drive", "drive": "drive",
    "ln": "lane", "ln.": "lane", "lane": "lane",
    "ct": "court", "ct.": "court", "court": "court",
    "pl": "place", "pl.": "place", "place": "place",
    "sq": "square", "sq.": "square", "square": "square",
    "trl": "trail", "trl.": "trail", "tr": "trail", "tr.": "trail", "trail": "trail",
    "pkwy": "parkway", "pkwy.": "parkway", "parkway": "parkway",
    "cir": "circle", "cir.": "circle", "circle": "circle",
    "ter": "terrace", "ter.": "terrace", "terrace": "terrace",
    "hwy": "highway", "hwy.": "highway", "highway": "highway",
    "way": "way", "loop": "loop",
    "cv": "cove", "cv.": "cove", "cove": "cove",
    "expy": "expressway", "expy.": "expressway", "expressway": "expressway",
    "aly": "alley", "aly.": "alley", "alley": "alley",

    # Directional prefixes
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "n.": "north", "s.": "south", "e.": "east", "w.": "west",
    "ne.": "northeast", "nw.": "northwest", "se.": "southeast", "sw.": "southwest"
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
        "confirmed_address": formatted_address,
        "fetch_address": True
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
        has_ranges = any(e["start"] is not None and e["end"] is not None for e in entries)

        # If the street has ranged entries, house_number is required
        if has_ranges:
            if house_number is None:
                print(f"Street '{street_name}' requires a valid house number")
                return {
                    "serviced": False,
                    "reason": f"Street '{street_name.title()}' requires a valid house number."
                }

            # Step 1: Check ranged entries
            for entry in entries:
                start, end = entry["start"], entry["end"]
                if start is not None and end is not None:
                    if start <= house_number <= end:
                        print(f"Range match → {entry['street']} ({entry['club']})")
                        return success_payload(entry, street_name, 100, formatted_address)

            # Street exists but number is invalid → HARD FAIL
            print(f"Rejected {house_number} on {street_name}: outside all ranges")
            return {
                "serviced": False,
                "reason": f"Street '{street_name.title()}' is recognized, but your address number is outside the serviced ranges."
            }

        # Step 2: Only allow catch-all if NO ranges exist
        if not has_ranges:
            if house_number is None:
                # House number missing → reject
                print(f"Street '{street_name}' has no ranges but number is missing")
                return {
                    "serviced": False,
                    "reason": f"Street '{street_name.title()}' requires a house number for verification."
                }
            # Accept any number for catch-all streets
            for entry in entries:
                print(f"Street match (no range) → {entry['street']} ({entry['club']})")
                return success_payload(entry, street_name, 100, formatted_address)

    # ---- fallback logic (street not found at all) ----
    # Fuzzy or token matches should NOT override number validation if the number exists
    if house_number is not None:
        print(f"No exact street match for '{street_name}' with number {house_number}")
        return {
            "serviced": False,
            "reason": f"No service information found for {house_number} {street_name.title()}"
        }

    # If no number provided, fuzzy street fallback is allowed
    tokens = set(norm_street.split())
    for entry in street_data:
        entry_tokens = set(normalize_street(entry["street"]).split())
        if tokens & entry_tokens:
            print(f"Token overlap → {entry['street']}")
            return success_payload(entry, street_name, 95, formatted_address)

    normalized_known = list(known_streets.keys())
    match = process.extractOne(norm_street, normalized_known)
    if not match:
        return None

    match_key, score = match
    if score >= 90:
        for entry in known_streets.get(match_key, []):
            print(f"Fuzzy (≥90) → {entry['street']} ({score}%)")
            return success_payload(entry, match_key, score, formatted_address)

    if score >= 80:
        match_tokens = set(match_key.split())
        if tokens & match_tokens:
            for entry in known_streets.get(match_key, []):
                print(f"Fuzzy (≥80) + token overlap → {entry['street']} ({score}%)")
                return success_payload(entry, match_key, score, formatted_address)

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
        return jsonify({"serviced": False, "reason": "Google API error"})

    if not data.get("results"):
        return jsonify({"serviced": False, "reason": "Address not found"})

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

    # Reject if user typed a number but Google couldn't parse it
    if re.search(r'\d', user_address) and house_number is None:
        return jsonify({
            "serviced": False,
            "reason": "Invalid or unrecognized street number"
        })

    if not street_name:
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    if city != "wichita falls" or state not in ("texas", "tx"):
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX"})

    norm_street = normalize_street(street_name)

    # Primary dataset
    result = match_address(primary_street_data, primary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result and result.get("serviced") is True:
        return jsonify(result)
    elif result and result.get("serviced") is False:
        return jsonify(result)

    # Secondary dataset
    result = match_address(secondary_street_data, secondary_known_streets, norm_street, house_number, street_name, formatted_address)
    if result and result.get("serviced") is True:
        return jsonify(result)
    elif result and result.get("serviced") is False:
        return jsonify(result)

    # No match
    combined_keys = list(primary_known_streets.keys()) + list(secondary_known_streets.keys())
    match = process.extractOne(norm_street, combined_keys)
    if not match:
        return jsonify({"serviced": False, "reason": "No known streets loaded"})

    combined_match, combined_score = match
    result = no_match_payload(norm_street, combined_match, combined_score)
    return jsonify(result)

@app.route('/')
def home():
    return "Rotary Club Lookup API is running with tiered matching and dual CSV fallback."


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
