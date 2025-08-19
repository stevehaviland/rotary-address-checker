from flask import Flask, request, jsonify
import csv
import re

app = Flask(__name__)

# Load address ranges from CSV
def load_address_ranges(filename):
    ranges = []
    with open(filename, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                start = int(row["START_NUM"])
                end = int(row["END_NUM"])
                street = row["STREET"].strip().lower()
                club = row["CLUB"].strip()
                ranges.append((start, end, street, club))
            except ValueError:
                continue
    return ranges

# Load both CSVs
primary_ranges = load_address_ranges("rotary_streets.csv")
secondary_ranges = load_address_ranges("rotary_extra.csv")

# Extract house number and street name
def parse_address(address):
    match = re.match(r"(\d+)\s+(.+)", address)
    if not match:
        return None, None
    number = int(match.group(1))
    street = match.group(2).strip().lower()
    return number, street

# Match against ranges
def match_address(number, street, ranges):
    for start, end, s, club in ranges:
        if s in street and start <= number <= end:
            return club
    return None

@app.route('/check', methods=['GET'])
def check_address():
    address = request.args.get("address", "").strip()
    token = request.args.get("token", "").strip()

    if token != "Gp#z86!FEVWlFU^nf0IT2@AW0@yWMrc^":
        return jsonify({"error": "Unauthorized"}), 403

    number, street = parse_address(address)
    if not number or not street:
        return jsonify({"error": "Invalid address format"}), 400

    # Tier 1: check primary CSV
    club = match_address(number, street, primary_ranges)
    if club:
        return jsonify({"club": club})

    # Tier 2: check secondary CSV
    club = match_address(number, street, secondary_ranges)
    if club:
        return jsonify({"club": club})

    # Not found in either file
    return jsonify({"error": "Address not found"}), 404


if __name__ == "__main__":
    app.run(debug=True)
