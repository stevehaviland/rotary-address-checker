from flask import Flask, request, jsonify
import csv
import requests
import os
import re
from fuzzywuzzy import process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Load token from environment
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
print(f"🚀 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

# Normalization function to reduce errors due to formatting
def normalize(text):
    return re.sub(r'\W+', '', text.lower().strip())

# Load streets and build reverse maps
street_to_club = {}
display_name_map = {}
known_streets = []

try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            raw_street = row['Street'].strip()
            normalized = normalize(raw_street)
            club = row['RotaryClub'].strip().upper()
            street_to_club[normalized] = club
            known_streets.append(normalized)
            display_name_map[normalized] = raw_street
except Exception as e:
    print("❌ Failed to load CSV:", e)

@app.route('/check', methods=['GET'])
def check_address():
    # Token auth via query param
    token = request.args.get('token', '')
    if token != AUTH_TOKEN:
        print("❌ Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    if not user_address:
        return jsonify({"error": "No address provided"}), 400

    print(f"🔍 User input: {user_address}")

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
    street_normalized = normalize(street_raw)

    city = address_info.get("city", "").lower().strip()
    state = address_info.get("state", "").lower().strip()

    print(f"📍 Parsed OSM street: '{street_raw}', city: '{city}', state: '{state}'")

    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX."})

    # Fuzzy match to normalized list
    match, score = process.extractOne(street_normalized, known_streets)
    print(f"🔁 Matched '{street_normalized}' → '{match}' with score {score}")

    if score >= 80:
        rotary_club = street_to_club[match]
        return jsonify({
            "serviced": True,
            "rotary_club": rotary_club,
            "matched_street": display_name_map.get(match, match.title()),
            "confidence_score": score
        })

    # Suggest up to 5 similar streets
    match_list = process.extract(street_normalized, known_streets, limit=5)
    suggestions = [
        {
            "street": display_name_map.get(norm, norm.title()),
            "score": s
        }
        for norm, s in match_list if s >= 60
    ]

    return jsonify({
        "serviced": False,
        "reason": f"No close match for '{street_raw}'.",
        "suggestions": suggestions
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
