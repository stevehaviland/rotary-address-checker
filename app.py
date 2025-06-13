from flask import Flask, request, jsonify
import csv
import requests
import os

app = Flask(__name__)

# Load street-to-club mapping
street_to_club = {}

# Load and normalize CSV data
try:
    with open('rotary_streets.csv', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            # Normalize headers and content
            cleaned_row = {k.strip().lower(): v for k, v in row.items()}
            street = cleaned_row.get('street', '').strip().lower()
            club = cleaned_row.get('rotaryclub', '').strip()
            if street and club:
                street_to_club[street] = club
except Exception as e:
    print("Failed to load CSV:", e)

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
    street_name = address_info.get("road", "").lower().strip()

    if not street_name:
        return jsonify({"serviced": False, "reason": "Could not extract street name"})

    if street_name in street_to_club:
        club = street_to_club[street_name]
        return jsonify({
            "serviced": True,
            "rotary_club": club,
            "street": street_name.title()
        })

    return jsonify({
        "serviced": False,
        "reason": f"{street_name.title()} is not in our service area."
    })

@app.route('/')
def home():
    return "✅ Rotary Club Lookup API is running."

# Required for Render deployment
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
