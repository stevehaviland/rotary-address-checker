from flask import Flask, request, jsonify
import csv
import requests
import os
from fuzzywuzzy import process

app = Flask(__name__)

# Load street-to-club mapping
street_to_club = {}
known_streets = []

try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Normalize headers and values
            cleaned_row = {k.strip().lower(): v for k, v in row.items()}
            street = cleaned_row.get('street', '').strip().lower()
            club = cleaned_row.get('rotaryclub', '').strip().upper()  # Normalize to uppercase
            if street and club:
                street_to_club[street] = club
                known_streets.append(street)
except Exception as e:
    print("❌ Failed to load CSV:", e)

@app.route('/check', methods=['GET'])
def check_address():
    user_address = request.args.get('address')
    if not user_address:
        return jsonify({"error": "No address provided"}), 400

    # Geocode with OpenStreetMap
    response = requests.get("https://nominatim.openstreetmap.org/search", params={
        "q": user_address,
        "format": "json",
        "addressdetails": 1,
        "limit": 1
    }, headers={"User-Agent": "RotaryClubLookup"})

    data = response.json()
    if not data or "address" not in data[0]:
        return jsonify({"serviced": False, "reason": "Address not found"})

    address_info = data[0]["address"]
    print("📍 Parsed address info:", address_info)  # Debug logging

    street_name = address_info.get("road", "").lower().strip()
    print(f"🔍 Extracted street name: '{street_name}'")  # Debug logging

    if not street_name:
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    # Fuzzy match the street name
    match, score = process.extractOne(street_name, known_streets)
    print(f"🔁 Matched to '{match}' with score {score}")  # Debug logging

    if score >= 75:  # Match threshold
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
    return "✅ Rotary Club Lookup API is running with fuzzy match enabled."

# Required for Render
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
