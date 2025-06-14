from flask import Flask, request, jsonify
import csv
import requests
import os
from fuzzywuzzy import process
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Load street-to-club mapping
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
    user_address = request.args.get('address')
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
        print("❌ No address found.")
        return jsonify({"serviced": False, "reason": "Address not found"})

    address_info = data[0]["address"]
    print("📍 Parsed address info:", address_info)

    street_name = address_info.get("road", "").lower().strip()
    city = address_info.get("city", "").lower().strip()
    state = address_info.get("state", "").lower().strip()

    print(f"🏙️ City: '{city}', State: '{state}'")
    if not street_name_
