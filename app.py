from flask import Flask, request, jsonify
import csv
import requests
import os
from fuzzywuzzy import process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Load auth token from environment
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
print(f"🚀 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

# Load the service street CSV
street_to_club = {}
known_streets = []

try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            cleaned_row = {k.strip().lower(): v for k, v in row.items()}
            street = cleaned_row.get('street', '').strip().lower()
            club = cleaned_row.get('rotaryclub', '').strip().upper()
            if street and club:
                street_to_club[street] = club
                known_streets.append(street)
except Exception as e:
    print("❌ Failed to load CSV:", e)

@app.route('/check', methods=['GET'])
def check_address():
    # ✅ Get and log the token
    token = request.args.get('token', '')
    print(f"🔐 Incoming token: {repr(token)}")

    if token != AUTH_TOKEN:
        print("❌ Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    print(f"🔍 User address input: {user_address}")

    response = requests.get("https://nominatim.openstreetmap.org/search", params={
        "q": user_address,
        "format": "json",
        "addressdetails": 1,
        "limit": 1
    }, headers={"User-Agent": "RotaryClubLookup"})

    data = response.json()
    print("📍 Raw geocode data:", data)

    if not data or "address" not in data[0]:
        return jsonify({"serviced": False, "reason": "Address not found"})

    address_info = data[0]["address"]
    print("📍 Parsed address info:", address_info)

    street_name = address_info.get("road", "").lower().strip()
    city = address_info.get("city", "").lower().strip()
    state = address_info.get("state", "").lower().strip()

    print(f"🏙️ City: '{city}', State: '{state}'")

    if not street_name:
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX"})

    match, score = process.extractOne(street_name, known_streets)
    print(f"🔁 Fuzzy matched to '{match}' with score {score}")

    if score >= 80:
        club = street_to_club[match]
        return jsonify({
            "serviced": True,
            "rotary_club": club,
            "matched_street": match.title(),
            "confidence_score": score
        })

    return jsonify({
        "serviced": False,
        "reason": f"No matching service street found for '{street_name.title()}'. Closest match: '{match.title()}' ({score}%)"
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running (token via query string)."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
