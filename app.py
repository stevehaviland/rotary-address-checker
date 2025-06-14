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

# Standard suffix replacements
SUFFIX_MAP = {
    "street": "st", "st.": "st",
    "avenue": "ave", "ave.": "ave",
    "drive": "dr", "dr.": "dr",
    "lane": "ln", "ln.": "ln",
    "court": "ct", "ct.": "ct",
    "road": "rd", "rd.": "rd",
    "boulevard": "blvd", "blvd.": "blvd"
}

# Normalize function with suffix handling
def normalize(text):
    text = text.lower()
    for word, abbr in SUFFIX_MAP.items():
        text = re.sub(rf"\b{word}\b", abbr, text)
    text = re.sub(r"[^a-z0-9 ]", "", text)  # preserve spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text

# In-memory storage for street data
street_to_club = {}
display_name_map = {}
known_streets = []

# Load CSV data
csv_file_path = "rotary_streets.csv"
if not os.path.exists(csv_file_path):
    raise FileNotFoundError(f"❌ CSV file not found: {csv_file_path}")

with open(csv_file_path, newline="") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        raw_street = row["Street"].strip()
        club = row["RotaryClub"].strip()
        norm = normalize(raw_street)
        street_to_club[norm] = club
        display_name_map[norm] = raw_street
        known_streets.append(norm)

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
            # Try fallback by checking similarity to other keys again
            for known_key in street_to_club:
                if fuzz.ratio(best_match, known_key) >= best_score:
                    rotary_club = street_to_club[known_key]
                    best_match = known_key
                    break

        print(f"✅ Match found: '{best_match}' (score: {best_score}) → Rotary: {rotary_club}")
        return jsonify({
            "serviced": True,
            "rotary_club": rotary_club,
            "matched_street": display_name_map.get(best_match, best_match.title()),
            "confidence_score": best_score
        })

    # Generate suggestions if no confident match
    print(f"🧐 No match found. Suggestions for '{street_raw}':")
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

@app.route("/suggest")
def suggest_partial():
    partial = request.args.get("partial", "").strip()
    token = request.args.get("token", "")

    if token != AUTH_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    if not partial:
        return jsonify({"suggestions": []})

    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": partial + ", Wichita Falls, TX", "format": "json", "addressdetails": 1, "limit": 5},
            headers={"User-Agent": "RotaryChecker/1.0"}
        )
        results = response.json()
    except Exception as e:
        return jsonify({"error": f"Geocoding error: {e}"}), 500

    matches = []
    for item in results:
        addr = item.get("address", {})
        street = addr.get("road", "").strip()
        if not street:
            continue

        norm_street = normalize(street)
        for known in known_streets:
            score = fuzz.ratio(norm_street, known)
            if score >= 75:
                matches.append({
                    "suggested": display_name_map.get(known, known.title()),
                    "match_score": score
                })

    matches = sorted(matches, key=lambda x: x["match_score"], reverse=True)
    return jsonify({"suggestions": matches[:5]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
