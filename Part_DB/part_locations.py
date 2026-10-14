"""User-owned drawer/storage labels, independent of schematic refreshes."""
from pathlib import Path
from db_core import read_json, write_json

LOCATIONS_PATH = Path(__file__).with_name("part_locations.json")


def load_locations(path=LOCATIONS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    document = read_json(path)
    if not isinstance(document, dict) or document.get("schema_version") != 1 or not isinstance(document.get("parts"), list):
        raise ValueError("Locations must have schema_version 1 and a parts list.")
    locations = {}
    for part in document["parts"]:
        if not isinstance(part, dict) or any(not isinstance(part.get(field), str) for field in ("name", "value", "pattern", "location")):
            raise ValueError("Each location needs name, value, pattern and location strings.")
        key = part["name"], part["value"], part["pattern"]
        if key in locations:
            raise ValueError("Duplicate part in location file.")
        locations[key] = part["location"]
    return locations


def save_locations(locations, path=LOCATIONS_PATH):
    write_json(path, {"schema_version": 1, "parts": [
        {"name": key[0], "value": key[1], "pattern": key[2], "location": location}
        for key, location in sorted(locations.items()) if location]})
