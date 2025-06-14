from flask import Flask, request, jsonify
import csv
import requests
import os
from fuzzywuzzy import process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Load auth token and Google API key from environment
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "your-google-api-key")
print(f"\U0001F680 Loaded AUTH_TOKEN: {repr(AUTH_TOKEN)}")

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
    token = request.args.get('token', '')
    print(f"🔐 Incoming token: {repr(token)}")

    if token != AUTH_TOKEN:
        print("❌ Token mismatch!")
        return jsonify({"error": "Unauthorized"}), 401

    user_address = request.args.get('address', '').strip()
    print(f"🔍 User address input: {user_address}")

    # Use Google Geocoding API
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": user_address,
        "key": GOOGLE_API_KEY
    }

    try:
        response = requests.get(geocode_url, params=params)
        response.raise_for_status()
        data = response.json()
        print("📍 Raw Google response:", data)
    except Exception as e:
        print("❌ Google API request failed:", e)
        return jsonify({"serviced": False, "reason": "Google API error"})

    if not data.get("results"):
        return jsonify({"serviced": False, "reason": "Address not found"})

    address_components = data["results"][0].get("address_components", [])
    street_name = ""
    city = ""
    state = ""

    for component in address_components:
        if "route" in component["types"]:
            street_name = component["long_name"].lower().strip()
        elif "locality" in component["types"]:
            city = component["long_name"].lower().strip()
        elif "administrative_area_level_1" in component["types"]:
            state = component["long_name"].lower().strip()

    print(f"🚏 Extracted: street='{street_name}', city='{city}', state='{state}'")

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
        "reason": f"No matching service street found for '{street_name.title()}'. Closest match: '{match.title()}' ({score}%)",
        "suggestions": [{"street": match.title(), "score": score}]
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running (Google Geocoding version)."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
