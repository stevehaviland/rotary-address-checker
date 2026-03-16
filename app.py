from flask import Flask, request, jsonify
import csv
import requests
import os
import re
from fuzzywuzzy import process, fuzz
from flask_cors import CORS
import logging
from typing import Dict, List, Optional, Tuple, Any
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuration
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme-123")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
PRIMARY_CSV = os.environ.get("PRIMARY_CSV", "rotary_streets.csv")
SECONDARY_CSV = os.environ.get("SECONDARY_CSV", "rotary_streets_extra.csv")

logger.info(f"Starting Rotary Club API with AUTH_TOKEN: {repr(AUTH_TOKEN)}")

# --- Street Abbreviation Mapping ---
ABBR_MAP = {
    # Street types
    "st": "street", "st.": "street", "street": "street",
    "rd": "road", "rd.": "road", "road": "road",
    "ave": "avenue", "ave.": "avenue", "avenue": "avenue",
    "blvd": "boulevard", "blvd.": "boulevard", "boulevard": "boulevard",
    "dr": "drive", "dr.": "drive", "drive": "drive",
    "ln": "lane", "ln.": "lane", "lane": "lane",
    "ct": "court", "ct.": "court", "court": "court",
    "cir": "circle", "cir.": "circle", "circle": "circle",
    "pl": "place", "pl.": "place", "place": "place",
    "sq": "square", "sq.": "square", "square": "square",
    "trl": "trail", "trl.": "trail", "tr": "trail", "tr.": "trail", "trail": "trail",
    "pkwy": "parkway", "pkwy.": "parkway", "parkway": "parkway",
    "ter": "terrace", "ter.": "terrace", "terrace": "terrace",
    "hwy": "highway", "hwy.": "highway", "highway": "highway",
    "way": "way", "loop": "loop",
    "cv": "cove", "cv.": "cove", "cove": "cove",
    "expy": "expressway", "expy.": "expressway", "expressway": "expressway",
    "aly": "alley", "aly.": "alley", "alley": "alley",
    
    # Directions
    "n": "north", "n.": "north", "north": "north",
    "s": "south", "s.": "south", "south": "south",
    "e": "east", "e.": "east", "east": "east",
    "w": "west", "w.": "west", "west": "west",
    "ne": "northeast", "ne.": "northeast", "northeast": "northeast",
    "nw": "northwest", "nw.": "northwest", "northwest": "northwest",
    "se": "southeast", "se.": "southeast", "southeast": "southeast",
    "sw": "southwest", "sw.": "southwest", "southwest": "southwest"
}

# Words to ignore when matching (flags, notes, etc.) - but preserve apartment indicators
IGNORE_WORDS = {
    'flag', 'flags', 'personal', 'sleeve', 'flowerbed', 'driveway', 
    'sidewalk', 'center', 'living', 'room', 'two', '2', '3', 'four', 
    '4', 'three', 'flags:'
}

# Apartment indicators to preserve
APARTMENT_INDICATORS = {'apt', 'apartment', 'unit', 'suite', '#'}
# Words that might be mistaken for apartment numbers
SKIP_WORDS = {'hwy', 'highway', 'road', 'rd', 'st', 'street', 'dr', 'drive', 'ln', 'lane', 'blvd', 'ave', 'avenue'}

class StreetEntry:
    """Represents a street entry from CSV"""
    def __init__(self, street_raw: str, club: str, start: Optional[int], end: Optional[int]):
        self.raw_street = street_raw
        self.club = club.upper()
        self.start = start
        self.end = end
        self.has_apartment = False
        self.apartment_number = None
        self.base_normalized = None
        self.normalized = self._normalize()
        self.base_name = self._extract_base_name()
        
    def _normalize(self) -> str:
        """Normalize street name for matching while preserving apartment info"""
        # Convert to lowercase
        s = self.raw_street.lower().strip()
        
        # Store original for debugging
        self.original_lower = s
        
        # Extract apartment information if present - DO THIS FIRST
        apt_patterns = [
            # Pattern for " - Apt 1302" at the end
            r'-\s*(?:apt|apartment|unit|suite)?\s*([a-z0-9]+)$',
            # Pattern for "Apt 1302" anywhere
            r'\b(apt|apartment|unit|suite)[\s\.#]*([a-z0-9]+)\b',
            # Pattern for "#1302"
            r'#\s*([a-z0-9]+)\b',
        ]
        
        for pattern in apt_patterns:
            apt_match = re.search(pattern, s, re.IGNORECASE)
            if apt_match:
                self.has_apartment = True
                # The apartment number could be in group 1 or 2 depending on pattern
                if len(apt_match.groups()) == 2:
                    self.apartment_number = apt_match.group(2).lower()
                else:
                    self.apartment_number = apt_match.group(1).lower()
                logger.info(f"  → Found apartment in entry: {self.raw_street} -> apt {self.apartment_number}")
                break
        
        # Remove parenthetical notes and flags info
        s = re.sub(r'\([^)]*\)', '', s)
        s = re.sub(r'[-]\s*\d+\s*flags?\b', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\b\d+\s*flags?\b', '', s, flags=re.IGNORECASE)
        s = re.sub(r'flags?:?\s*\w*', '', s, flags=re.IGNORECASE)
        
        # Remove other noise words but PRESERVE apartment indicators
        words = s.split()
        filtered_words = []
        i = 0
        while i < len(words):
            w = words[i]
            # Check if this is apartment related
            is_apt_indicator = w in APARTMENT_INDICATORS
            is_apt_number = w.replace('.', '').isdigit() and self.has_apartment
            
            if is_apt_indicator or is_apt_number:
                filtered_words.append(w)
            elif w not in IGNORE_WORDS:
                filtered_words.append(w)
            i += 1
        
        s = ' '.join(filtered_words)
        
        # Remove special characters but keep hyphens for apartment numbers
        s = re.sub(r'[^\w\s-]', ' ', s)
        
        # Normalize abbreviations (but preserve apartment info)
        tokens = s.split()
        normalized_tokens = []
        for t in tokens:
            # Check if this is apartment related
            if t in APARTMENT_INDICATORS or (t.replace('.', '').isdigit() and self.has_apartment):
                normalized_tokens.append(t)
            else:
                normalized_tokens.append(ABBR_MAP.get(t, t))
        
        # Remove extra spaces
        normalized = ' '.join(normalized_tokens).strip()
        
        # For apartment entries, create a unique normalized key that includes the apartment number
        if self.has_apartment and self.apartment_number:
            # Create a base normalized name without apartment info
            base_normalized = normalized
            # Remove apartment number and indicator from base
            base_normalized = re.sub(r'\b(?:apt|apartment|unit|suite)\b', '', base_normalized)
            base_normalized = re.sub(r'\b' + re.escape(self.apartment_number) + r'\b', '', base_normalized)
            base_normalized = re.sub(r'\s+', ' ', base_normalized).strip()
            self.base_normalized = base_normalized
            
            # For fuzzy matching, use base_normalized
            # For exact matching, use full key with apartment
            return f"{base_normalized}|apt:{self.apartment_number}"
        
        return normalized
    
    def _extract_base_name(self) -> str:
        """Extract base street name without numbers, directions, or apartment info"""
        if self.has_apartment and self.base_normalized:
            words = self.base_normalized.split()
        else:
            words = self.normalized.split()
            
        # Remove direction words for base matching
        base_words = [w for w in words if w not in {'north', 'south', 'east', 'west'}]
        return ' '.join(base_words)
    
    def matches_number(self, house_number: int) -> bool:
        """Check if house number falls within range"""
        # If no range defined, accept any number
        if self.start is None or self.end is None:
            logger.info(f"  → No range defined for {self.raw_street}, accepting {house_number}")
            return True
        
        # Make sure we're comparing integers correctly
        # The range should be inclusive of both start and end
        result = self.start <= house_number <= self.end
        logger.info(f"  → Checking {house_number} against {self.raw_street}: {self.start} <= {house_number} <= {self.end} = {result}")
        return result
    
    def matches_apartment(self, apt_number: str) -> bool:
        """Check if apartment number matches"""
        if not self.has_apartment or not apt_number:
            return False
        return self.apartment_number == apt_number.lower()
    
    def is_single_address(self) -> bool:
        """Check if this is a single address (not a range)"""
        return self.start is not None and self.end is not None and self.start == self.end
    
    def __repr__(self) -> str:
        apt_info = f" apt:{self.apartment_number}" if self.has_apartment else ""
        return f"StreetEntry(raw='{self.raw_street}', club='{self.club}', range={self.start}-{self.end}{apt_info})"

class StreetDatabase:
    """Manages street data with efficient lookup"""
    
    def __init__(self, csv_files: List[str]):
        self.entries: List[StreetEntry] = []
        self.by_normalized: Dict[str, List[StreetEntry]] = {}
        self.by_base: Dict[str, List[StreetEntry]] = {}
        self.by_apartment: Dict[str, List[StreetEntry]] = {}  # Index by apartment number
        self.all_normalized: List[str] = []
        
        for csv_file in csv_files:
            self._load_csv(csv_file)
        
        logger.info(f"Loaded total {len(self.entries)} entries from {len(csv_files)} files")
        logger.info(f"Normalized streets: {len(self.by_normalized)} unique keys")
        logger.info(f"Base names: {len(self.by_base)} unique")
        
        apt_count = sum(1 for e in self.entries if e.has_apartment)
        logger.info(f"Apartment entries: {apt_count}")
        
        # Log all Jacksboro entries for debugging
        jacksboro_entries = [e for e in self.entries if 'jacksboro' in e.raw_street.lower()]
        logger.info(f"Total Jacksboro entries: {len(jacksboro_entries)}")
        for e in jacksboro_entries:
            logger.info(f"  - {e.raw_street} (apt:{e.apartment_number}) normalized: '{e.normalized}'")
    
    def _load_csv(self, filename: str):
        """Load and parse CSV file"""
        try:
            with open(filename, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                if not reader.fieldnames:
                    logger.error(f"No headers found in {filename}")
                    return
                
                # Normalize headers
                fieldnames = [f.strip().lower() for f in reader.fieldnames]
                logger.info(f"Loading {filename} with headers: {fieldnames}")
                
                count = 0
                for row in reader:
                    # Clean row keys
                    cleaned_row = {}
                    for k, v in row.items():
                        if k is not None:
                            cleaned_row[k.strip().lower()] = v.strip() if v else ''
                    
                    # Extract fields
                    street_raw = cleaned_row.get('street', '')
                    if not street_raw:
                        continue
                    
                    club = cleaned_row.get('rotaryclub', '').upper() or 'UNKNOWN'
                    
                    # Parse address numbers
                    start_str = cleaned_row.get('start_address', '')
                    end_str = cleaned_row.get('end_address', '')
                    
                    start = self._parse_int(start_str)
                    end = self._parse_int(end_str)
                    
                    # Create entry
                    entry = StreetEntry(street_raw, club, start, end)
                    self.entries.append(entry)
                    
                    # Debug parsing for Jacksboro entries
                    if 'jacksboro' in street_raw.lower():
                        logger.info(f"Parsing Jacksboro entry: '{street_raw}' -> apt:{entry.has_apartment} num:{entry.apartment_number}")
                    
                    # Index by normalized name
                    norm = entry.normalized
                    if norm:
                        if norm not in self.by_normalized:
                            self.by_normalized[norm] = []
                        self.by_normalized[norm].append(entry)
                        
                        # Track unique normalized names for fuzzy matching (skip apartment-specific keys)
                        if norm not in self.all_normalized and '|apt:' not in norm:
                            self.all_normalized.append(norm)
                    
                    # Also index by base normalized for apartment entries
                    if entry.has_apartment and entry.base_normalized:
                        base_norm = entry.base_normalized
                        if base_norm:
                            if base_norm not in self.by_normalized:
                                self.by_normalized[base_norm] = []
                            self.by_normalized[base_norm].append(entry)
                            
                            # Also add to fuzzy matching list if not already there
                            if base_norm not in self.all_normalized:
                                self.all_normalized.append(base_norm)
                    
                    # Index by base name
                    base = entry.base_name
                    if base and base != norm and base != entry.base_normalized:
                        if base not in self.by_base:
                            self.by_base[base] = []
                        self.by_base[base].append(entry)
                    
                    # Index by apartment number if present
                    if entry.has_apartment and entry.apartment_number:
                        if entry.apartment_number not in self.by_apartment:
                            self.by_apartment[entry.apartment_number] = []
                        self.by_apartment[entry.apartment_number].append(entry)
                    
                    count += 1
                
                logger.info(f"Loaded {count} entries from {filename}")
                # Log apartment entries count
                apt_count = sum(1 for e in self.entries if e.has_apartment)
                logger.info(f"  → {apt_count} apartment entries in {filename}")
                
        except FileNotFoundError:
            logger.error(f"CSV file not found: {filename}")
        except Exception as e:
            logger.error(f"Error loading {filename}: {e}", exc_info=True)
    
    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        """Safely parse integer from string"""
        if not value or value == '':
            return None
        # Remove any non-digit characters
        cleaned = re.sub(r'[^\d-]', '', value)
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            return None
    
    def extract_apartment_from_query(self, query: str) -> Tuple[Optional[str], str]:
        """Extract apartment number from query string"""
        query_lower = query.lower()
        
        # More specific patterns for apartment indicators
        patterns = [
            # apt 703, apartment 703, unit 703, suite 703, #703
            r'\b(?:apt|apartment|unit|suite)[\s\.#]*([a-z0-9]{1,10})\b',
            r'#\s*([a-z0-9]{1,10})\b',
            # - Apt 703, -703 (at end of string)
            r'-\s*(?:apt|apartment|unit|suite)?\s*([a-z0-9]{1,10})$',
            # space then number at end (but only if it's 1-5 chars and not a common word)
            r'\s+([a-z0-9]{1,5})$',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                apt_num = match.group(1).lower()
                # Validate it's not a common street word
                if apt_num in SKIP_WORDS:
                    logger.info(f"Skipping '{apt_num}' - common street word")
                    continue
                # Remove the apartment part from the query for street matching
                clean_query = re.sub(pattern, '', query_lower, count=1).strip()
                clean_query = re.sub(r'\s+', ' ', clean_query).strip()
                logger.info(f"Extracted apartment '{apt_num}' from query, remaining: '{clean_query}'")
                return apt_num, clean_query
        
        return None, query
    
    def find_exact_match(self, normalized: str, house_number: Optional[int], full_query: str = "") -> Tuple[Optional[StreetEntry], str]:
        """Find exact match by normalized street name"""
        # Extract apartment from query if present
        apt_number, clean_query = self.extract_apartment_from_query(full_query)
        
        # If we have an apartment number, try exact apartment match first
        if apt_number:
            apt_key = f"{normalized}|apt:{apt_number}"
            logger.info(f"Looking for apartment key: '{apt_key}'")
            entries = self.by_normalized.get(apt_key, [])
            if entries:
                logger.info(f"Found exact apartment match for {apt_key}")
                for entry in entries:
                    if house_number is None or entry.matches_number(house_number):
                        return entry, "exact_apartment"
        
        # Then try regular normalized match
        entries = self.by_normalized.get(normalized, [])
        if not entries:
            return None, "no_match"
        
        # Log all entries for debugging
        if 'jacksboro' in normalized:
            logger.info(f"Found {len(entries)} Jacksboro entries for '{normalized}':")
            for e in entries:
                apt_info = f" (apt:{e.apartment_number})" if e.has_apartment else ""
                logger.info(f"  - {e.raw_street}: range {e.start}-{e.end}{apt_info}")
        
        # If no house number, return first entry with note
        if house_number is None:
            if any(e.start is None for e in entries):
                return entries[0], "exact_no_number"
            return None, "need_number"
        
        # First, try to match apartment entries if the query has apartment info
        if apt_number:
            logger.info(f"Looking for apartment {apt_number} in entries")
            for entry in entries:
                if entry.has_apartment and entry.matches_apartment(apt_number):
                    if entry.matches_number(house_number):
                        logger.info(f"  → APARTMENT MATCH: {entry.raw_street} for apt {apt_number}")
                        return entry, "exact_apartment"
                    else:
                        logger.info(f"  → Apartment {apt_number} found but number {house_number} outside range {entry.start}-{entry.end}")
        
        # Then check regular entries (non-apartment first)
        for entry in entries:
            if entry.has_apartment:
                continue  # Skip apartment entries for now
                
            logger.info(f"Checking {house_number} against {entry.raw_street} range {entry.start}-{entry.end}")
            
            if entry.start is None or entry.end is None:
                logger.info(f"  → No range, accepting {house_number}")
                return entry, "exact_no_range"
            
            if entry.matches_number(house_number):
                logger.info(f"  → MATCH: {entry.start} <= {house_number} <= {entry.end}")
                return entry, "exact_range"
            else:
                logger.info(f"  → NO MATCH: {house_number} is outside {entry.start}-{entry.end}")
        
        # Finally check apartment entries (without apartment number match)
        for entry in entries:
            if not entry.has_apartment:
                continue
                
            logger.info(f"Checking apartment entry {entry.raw_street} with number {house_number}")
            if entry.matches_number(house_number):
                logger.info(f"  → Apartment entry matched by number: {entry.raw_street}")
                return entry, "exact_apartment_number_only"
        
        logger.info(f"Street found but number {house_number} out of all ranges")
        return None, "out_of_range"
    
    def find_base_match(self, base_name: str, house_number: Optional[int], full_query: str = "") -> Tuple[Optional[StreetEntry], str, int]:
        """Find match by base street name"""
        entries = self.by_base.get(base_name, [])
        if not entries:
            return None, "no_match", 0
        
        # Extract apartment from query if present
        apt_number, _ = self.extract_apartment_from_query(full_query)
        
        # Calculate confidence based on name similarity
        name_score = 95  # High confidence for base match
        
        # Try apartment match first
        if apt_number:
            for entry in entries:
                if entry.has_apartment and entry.matches_apartment(apt_number):
                    if house_number is None or entry.matches_number(house_number):
                        return entry, "base_apartment", name_score
        
        if house_number is None:
            if any(e.start is None for e in entries):
                return entries[0], "base_no_number", name_score
            return None, "need_number", name_score
        
        # Check ranges
        for entry in entries:
            if entry.matches_number(house_number):
                return entry, "base_range", name_score
        
        return None, "out_of_range", name_score
    
    def find_fuzzy_match(self, normalized: str, threshold: int = 80) -> Tuple[Optional[StreetEntry], int]:
        """Find fuzzy match on normalized names"""
        if not self.all_normalized:
            return None, 0
        
        # Try different matching strategies
        best_match = None
        best_score = 0
        
        # Strategy 1: Token sort ratio (good for word order variations)
        for norm in self.all_normalized:
            score = fuzz.token_sort_ratio(normalized, norm)
            if score > best_score:
                best_score = score
                best_match = norm
        
        # Strategy 2: Partial ratio (good for substring matches)
        if best_score < 90:
            for norm in self.all_normalized:
                score = fuzz.partial_ratio(normalized, norm)
                if score > best_score:
                    best_score = score
                    best_match = norm
        
        if best_score >= threshold and best_match:
            return self.by_normalized[best_match][0], best_score
        
        return None, best_score

# Initialize databases
try:
    primary_db = StreetDatabase([PRIMARY_CSV])
    secondary_db = StreetDatabase([SECONDARY_CSV])
    logger.info("Databases initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize databases: {e}")
    primary_db = StreetDatabase([])
    secondary_db = StreetDatabase([])

def parse_google_address(components: List[Dict]) -> Dict[str, Any]:
    """Parse Google Geocoding API response components"""
    result = {
        'house_number': None,
        'street_name': '',
        'city': '',
        'state': '',
        'formatted': ''
    }
    
    for c in components:
        types = c.get('types', [])
        if 'street_number' in types:
            try:
                result['house_number'] = int(c['long_name'])
            except:
                pass
        elif 'route' in types:
            result['street_name'] = c['long_name'].lower().strip()
        elif 'locality' in types:
            result['city'] = c['long_name'].lower().strip()
        elif 'administrative_area_level_1' in types:
            result['state'] = c['long_name'].lower().strip()
    
    return result

def create_response(serviced: bool, **kwargs) -> Dict:
    """Create standardized response"""
    response = {"serviced": serviced}
    response.update(kwargs)
    return response

@app.route('/check', methods=['GET'])
def check_address():
    """Main endpoint for address checking"""
    # Authentication
    token = request.args.get('token', '')
    if token != AUTH_TOKEN:
        logger.warning(f"Unauthorized access attempt with token: {token}")
        return jsonify({"error": "Unauthorized"}), 401
    
    # Get address
    user_address = request.args.get('address', '').strip()
    if not user_address:
        return jsonify(create_response(False, reason="No address provided")), 400
    
    logger.info(f"Checking address: {user_address}")
    
    # Geocode address
    try:
        geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": user_address,
            "key": GOOGLE_API_KEY,
            "components": "administrative_area:TX|locality:Wichita_Falls"
        }
        
        response = requests.get(geocode_url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
    except requests.exceptions.Timeout:
        logger.error("Google API timeout")
        return jsonify(create_response(False, reason="Geocoding service timeout")), 503
    except requests.exceptions.RequestException as e:
        logger.error(f"Google API error: {e}")
        return jsonify(create_response(False, reason="Geocoding service unavailable")), 503
    except Exception as e:
        logger.error(f"Unexpected error during geocoding: {e}")
        return jsonify(create_response(False, reason="Address validation failed")), 500
    
    # Check geocoding results
    if data.get('status') != 'OK' or not data.get('results'):
        logger.info(f"Address not found: {user_address}")
        return jsonify(create_response(False, reason="Address not found"))
    
    # Parse address components
    result = data['results'][0]
    components = result.get('address_components', [])
    parsed = parse_google_address(components)
    parsed['formatted'] = result.get('formatted_address', '')
    
    # Validate location
    if parsed['city'] != 'wichita falls' or parsed['state'] not in ('texas', 'tx'):
        logger.info(f"Address outside service area: {parsed['city']}, {parsed['state']}")
        return jsonify(create_response(False, reason="We only service Wichita Falls, TX"))
    
    if not parsed['street_name']:
        return jsonify(create_response(False, reason="Could not extract street name"))
    
    # Check if address has number but we couldn't extract it
    if re.search(r'\d', user_address) and parsed['house_number'] is None:
        return jsonify(create_response(False, reason="Invalid or unrecognized street number"))
    
    # Normalize street name
    normalized_street = normalize_street(parsed['street_name'])
    base_street = ' '.join([w for w in normalized_street.split() 
                           if w not in {'north', 'south', 'east', 'west'}])
    
    logger.info(f"Looking up: {parsed['house_number']} {normalized_street} (base: {base_street})")
    
    # Try primary database first with full address for apartment matching
    entry, match_type = primary_db.find_exact_match(normalized_street, parsed['house_number'], user_address)
    if entry:
        return handle_match(entry, match_type, parsed, user_address)
    
    # If no exact match in primary, try base name in primary
    if base_street != normalized_street:
        entry, match_type, score = primary_db.find_base_match(base_street, parsed['house_number'], user_address)
        if entry:
            return handle_match(entry, match_type, parsed, score, user_address)
    
    # Try secondary database
    entry, match_type = secondary_db.find_exact_match(normalized_street, parsed['house_number'], user_address)
    if entry:
        return handle_match(entry, match_type, parsed, 100, user_address)
    
    # Try base name in secondary
    if base_street != normalized_street:
        entry, match_type, score = secondary_db.find_base_match(base_street, parsed['house_number'], user_address)
        if entry:
            return handle_match(entry, match_type, parsed, score, user_address)
    
    # Try fuzzy matching as last resort
    entry, score = primary_db.find_fuzzy_match(normalized_street)
    if not entry:
        entry, score = secondary_db.find_fuzzy_match(normalized_street)
    
    if entry and score >= 85:
        # For fuzzy matches, we still need to check the house number against ranges
        if parsed['house_number'] is not None:
            if not entry.matches_number(parsed['house_number']):
                logger.info(f"Fuzzy match found but number {parsed['house_number']} outside range")
                return jsonify(create_response(
                    serviced=False,
                    reason=f"Street '{entry.raw_street}' is recognized, but address {parsed['house_number']} is outside serviced ranges.",
                    rotary_club=entry.club,
                    matched_street=entry.raw_street,
                    suggestions=[{"street": entry.raw_street, "score": score}]
                ))
        
        logger.info(f"Fuzzy match found: {entry.raw_street} (score: {score})")
        return jsonify(create_response(
            serviced=True,
            rotary_club=entry.club,
            matched_street=entry.raw_street,
            confidence_score=score,
            confirmed_address=parsed['formatted'],
            fetch_address=True,
            match_type="fuzzy"
        ))
    
    # No match found
    suggestions = []
    if entry:
        suggestions.append({
            "street": entry.raw_street,
            "score": score
        })
    
    return jsonify(create_response(
        serviced=False,
        reason=f"No service found for {parsed['house_number'] or ''} {parsed['street_name'].title()}",
        suggestions=suggestions
    ))

def handle_match(entry: StreetEntry, match_type: str, parsed: Dict, score: int = 100, original_query: str = "") -> Tuple[Dict, int]:
    """Handle successful match and return appropriate response"""
    
    if match_type == "out_of_range":
        logger.info(f"Address {parsed['house_number']} outside range for {entry.raw_street}")
        return jsonify(create_response(
            serviced=False,
            reason=f"Street is recognized, but address {parsed['house_number']} is outside serviced ranges.",
            rotary_club=entry.club,
            matched_street=entry.raw_street
        )), 200
    
    if match_type == "need_number":
        logger.info(f"Street {entry.raw_street} requires house number")
        return jsonify(create_response(
            serviced=False,
            reason=f"Street is recognized but requires a house number for verification.",
            rotary_club=entry.club,
            matched_street=entry.raw_street
        )), 200
    
    # For matches that have a range, verify the number is within it
    if parsed['house_number'] is not None and entry.start is not None and entry.end is not None:
        if not entry.matches_number(parsed['house_number']):
            logger.info(f"Match found but number {parsed['house_number']} outside range {entry.start}-{entry.end}")
            return jsonify(create_response(
                serviced=False,
                reason=f"Street is recognized, but address {parsed['house_number']} is outside serviced ranges.",
                rotary_club=entry.club,
                matched_street=entry.raw_street
            )), 200
    
    # For apartment matches, add apartment info to response
    response_data = {
        "serviced": True,
        "rotary_club": entry.club,
        "matched_street": entry.raw_street,
        "confidence_score": score,
        "confirmed_address": parsed['formatted'],
        "fetch_address": True,
        "match_type": match_type
    }
    
    # If this was an apartment match, include that info
    if entry.has_apartment:
        response_data["apartment_number"] = entry.apartment_number
        response_data["note"] = "This is a specific apartment address"
    
    # Successful match
    logger.info(f"Match found: {entry.raw_street} ({entry.club}) via {match_type}")
    return jsonify(response_data), 200

def normalize_street(s: str) -> str:
    """Normalize street name (standalone function for compatibility)"""
    s = s.lower().strip()
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\b(apt|suite|unit|flags?|personal)\b\s*\w*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    tokens = s.split()
    tokens = [ABBR_MAP.get(t, t) for t in tokens]
    return ' '.join(tokens).strip()

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "primary_entries": len(primary_db.entries),
        "secondary_entries": len(secondary_db.entries),
        "version": "3.2"
    })

@app.route('/')
def home():
    return jsonify({
        "name": "Rotary Club Address Lookup API",
        "version": "3.2",
        "endpoints": {
            "/check": "GET - Check if an address is serviced (requires token parameter)",
            "/health": "GET - Health check"
        },
        "features": [
            "Apartment number matching",
            "Address range validation",
            "Fuzzy street name matching",
            "Dual CSV support"
        ],
        "status": "running"
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.run(host='0.0.0.0', port=port, debug=debug)