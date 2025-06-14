import os
import csv
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from fuzzywuzzy import fuzz
import requests

app = Flask(__name__)
CORS(app)

# Load the auth token from environment variable
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "Gp#z86!FEVWlFU^nf0IT2@AW0@yWMrc^")

# In-memory storage for street data
street_to_club = {}
display_name_map = {}
known_streets = []

# Load CSV data
with open("rotary_streets.csv", newline="") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        raw_street = row["Street"].strip()
        club = row["RotaryClub"].strip()
        norm = re.sub(r"[^\w]", "", raw_street.lower())
        street_to_club[norm] = club
        display_name_map[norm] = raw_street
        known_streets.append(norm)

# Helper normalization
def normalize(text):
    text = text.lower()
    text = re.sub(r"[^\w]", "", text)
    return text.strip()

@app.route("/")
def home():
    return "Rotary Address Checker API is running."

@app.route("/check")
def check_address():
    address = request.args.get("address", "")
    token = request.args.get("token", "")

    if token != AUTH_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    if not address:
        return jsonify({"error": "Missing address parameter"}), 400

    # Use OpenStreetMap to resolve the address
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "addressdetails": 1, "limit": 1},
            headers={"User-Agent": "RotaryChecker/1.0"}
        )
        data = response.json()
    except Exception as e:
        return jsonify({"error": f"Geocoding failed: {e}"}), 500

    if not data:
        return jsonify({"serviced": False, "reason": "Address not found."})

    address_data = data[0].get("address", {})
    street_raw = address_data.get("road", "").strip()
    city = address_data.get("city", "") or address_data.get("town", "")
    state = address_data.get("state", "")

    if city.lower() != "wichita falls" or state.lower() != "texas":
        return jsonify({"serviced": False, "reason": "We currently only support Wichita Falls, TX."})

    input_norm = normalize(street_raw)

    # Fuzzy match logic
    best_match = None
    best_score = 0
    for known in known_streets:
        score = fuzz.ratio(input_norm, known)
        if score > best_score:
            best_score = score
            best_match = known

    print(f"🔍 Best fuzzy match: '{best_match}' ({best_score}) vs. input: '{input_norm}'")

    # If match is confident enough, return success
    if best_score >= 80:
        rotary_club = street_to_club.get(best_match)
        if not rotary_club:
            rotary_club = street_to_club.get(best_match.replace(" ", ""), "UNKNOWN")
        print(f"✅ Match found: '{best_match}' (score: {best_score}) → Rotary: {rotary_club}")
        return jsonify({
            "serviced": True,
            "rotary_club": rotary_club,
            "matched_street": display_name_map.get(best_match, best_match.title()),
            "confidence_score": best_score
        })

    # Generate suggestions if no confident match
    print(f"🧠 No match found. Suggestions for '{street_raw}':")
    suggestion_candidates = []
    seen = set()

    for known in known_streets:
        score = fuzz.ratio(input_norm, known)
        display_name = display_name_map.get(known, known.title())
        if score >= 60 and display_name not in seen:
            suggestion_candidates.append({
                "street": display_name,
                "score": score
            })
            seen.add(display_name)

    suggestion_candidates.sort(key=lambda x: x["score"], reverse=True)
    top_suggestions = suggestion_candidates[:5]

    for s in top_suggestions:
        print(f"   → {s['street']} (score: {s['score']})")

    return jsonify({
        "serviced": False,
        "reason": f"No close match for '{street_raw}'.",
        "suggestions": top_suggestions
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
