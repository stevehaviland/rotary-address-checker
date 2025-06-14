from flask import Flask, request, jsonify
import csv
import requests
import os
import re
from fuzzywuzzy import fuzz
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
print(f"🚀 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

def normalize(text):
    """Lowercase, remove punctuation, and collapse whitespace."""
    t = re.sub(r'[^\w\s]', ' ', text.lower())
    return re.sub(r'\s+', ' ', t).strip()

# Data storage
street_to_club = {}
display_name_map = {}
known_streets = []

# Load and normalize CSV
try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            raw_street = row['Street'].strip()
            club = row['RotaryClub'].strip().upper()

            norm_with_space = normalize(raw_street)
            norm_no_space = norm_with_space.replace(" ", "")

            for key in {norm_with_space, norm_no_space}:
                street_to_club[key] = club
                display_name_map[key] = raw_street
                known_streets.append(key)
except Exception as e:
    print("❌ Failed to load CSV:", e)

@app.route('/check', methods=['GET'])
def check_address():
    token = request.args.get('token', '')
    if token != AUTH_TOKEN:
        print("❌ Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    if not user_address:
        return jsonify({"error": "No address provided"}), 400

    print(f"🔍 Checking address: {user_address}")

    # Lookup via OpenStreetMap
    response = requests.get("https://nominatim.openstreetmap.org/search", params={
        "q": user_address,
        "format": "json",
        "addressdetails": 1,
        "limit": 1
    }, headers={"User-Agent": "RotaryClubLookup"})

    data = response.json()
    if not data or "address" not in data[0]:
        return jsonify({"serviced": False, "reason": "Address not found."})

    address_info = data[0]["address"]
    street_raw = address_info.get("road", "").strip()
    city = address_info.get("city", "").lower().strip()
    state = address_info.get("state", "").lower().strip()

    if not street_raw:
        return jsonify({"serviced": False, "reason": "No street name found in address."})

    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX."})

    print(f"📍 Parsed street: {street_raw}, city: {city}, state: {state}")

    # Normalize input
    input_norm = normalize(street_raw)
    input_nospace = input_norm.replace(" ", "")

    # Fuzzy match
    all_variants = set(known_streets + [s.replace(" ", "") for s in known_streets])
    best_match = None
    best_score = 0

    for variant in all_variants:
        score = fuzz.ratio(input_nospace, variant)
        if score > best_score:
            best_score = score
            best_match = variant

    print(f"🔁 Fuzzy matched '{input_nospace}' to '{best_match}' with score {best_score}")

    if best_score >= 80:
        rotary_club = street_to_club.get(best_match, street_to_club.get(best_match.replace(" ", ""), "UNKNOWN"))
        return jsonify({
            "serviced": True,
            "rotary_club": rotary_club,
            "matched_street": display_name_map.get(best_match, best_match.title()),
            "confidence_score": best_score
        })

    # Suggestion fallback
    suggestion_candidates = []
    seen_display_names = set()

    for variant in known_streets:
        score = fuzz.ratio(input_nospace, variant.replace(" ", ""))
        display_name = display_name_map.get(variant, variant.title())

        if score >= 60 and display_name not in seen_display_names:
            suggestion_candidates.append({
                "street": display_name,
                "score": score
            })
            seen_display_names.add(display_name)

    suggestion_candidates.sort(key=lambda x: x["score"], reverse=True)

    return jsonify({
        "serviced": False,
        "reason": f"No close match for '{street_raw}'.",
        "suggestions": suggestion_candidates[:5]
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
