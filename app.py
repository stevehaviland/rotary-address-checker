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

try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            cleaned_row = {k.strip().lower(): v.strip() for k, v in row.items()}
            street = cleaned_row.get('street', '').lower()
            club = cleaned_row.get('rotaryclub', '').upper()
            start_address = cleaned_row.get('start_address', '')
            end_address = cleaned_row.get('end_address', '')
            if street and club:
                known_streets.append(street)
                street_data.append({
                    "street": street,
                    "club": club,
                    "start": int(start_address) if start_address.isdigit() else None,
                    "end": int(end_address) if end_address.isdigit() else None
                })
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
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    if city != "wichita falls" or state != "texas":
        return jsonify({"serviced": False, "reason": "We only service Wichita Falls, TX"})

    match, score = process.extractOne(street_name, known_streets)
    print(f"🔁 Fuzzy matched to '{match}' with score {score}")

    # Stricter validation
    accept_match = False
    if score >= 90:
        accept_match = True
    else:
        # Require shared token
        input_tokens = set(street_name.split())
        match_tokens = set(match.split())
        if input_tokens & match_tokens:
            accept_match = True

    if accept_match:
        for entry in street_data:
            if entry["street"] == match:
                start = entry["start"]
                end = entry["end"]
                if start is not None and end is not None and house_number is not None:
                    if start <= house_number <= end:
                        return jsonify({
                            "serviced": True,
                            "rotary_club": entry["club"],
                            "matched_street": match.title(),
                            "confidence_score": score,
                            "confirmed_address": formatted_address
                        })
                else:
                    return jsonify({
                        "serviced": True,
                        "rotary_club": entry["club"],
                        "matched_street": match.title(),
                        "confidence_score": score,
                        "confirmed_address": formatted_address
                    })

    # No acceptable match
    return jsonify({
        "serviced": False,
        "reason": f"No matching service street found for '{street_name.title()}'. Closest match: '{match.title()}' ({score}%)",
        "suggestions": [{"street": match.title(), "score": score}]
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running (strict fuzzy matching)."

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)