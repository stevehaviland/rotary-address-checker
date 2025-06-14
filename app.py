from flask import Flask, request, jsonify
import csv
import requests
import os
import re
from fuzzywuzzy import fuzz, process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
print(f"🚀 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

def normalize(text):
    """Lowercase, remove punctuation, and collapse whitespace."""
    t = re.sub(r'[^\w\s]', ' ', text.lower())
    return re.sub(r'\s+', ' ', t).strip()

# Prepare street maps
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
    city = address_info.get("city", "").lower().strip()
    state = address_info.get("state", "").lower().strip()

    if not street_raw:
        return jsonify({"serviced": False, "reason": "No street name found in address."})
    
    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX."})

    print(f"📍 Parsed street: {street_raw}, city: {city}, state: {state}")

    street_norm = normalize(street_raw)

    # Match using fuzz.token_set_ratio
    scored_matches = [(s, fuzz.token_set_ratio(street_norm, s)) for s in known_streets]
    scored_matches.sort(key=lambda x: x[1], reverse=True)
    match, score = scored_matches[0]

    print(f"🔁 Fuzzy matched '{street_raw}' → '{match}' with score {score}")

    if score >= 80:
        rotary_club = street_to_club[match]
        return jsonify({
            "serviced": True,
            "rotary_club": rotary_club,
            "matched_street": display_name_map.get(match, match.title()),
            "confidence_score": score
        })

    # Provide fallback suggestions
    suggestions = []
    for s, s_score in scored_matches[:5]:
        if s_score >= 60:
            suggestions.append({
                "street": display_name_map.get(s, s.title()),
                "score": s_score
            })

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
