"""
huff_engine_v3.py
Refactored Huff Model engine that queries the SQLite database (urban_ai_v2) instead of reading flat CSV/GeoJSON files.

All file readers (pd.read_csv, json.load) have been removed.
All SQL queries use parameterized placeholders (?) to prevent SQL injection.

Team members: Wilson ChungNam Wan, Rojisha Awale, Sharelle Allen, Omotolani Orekoya
Date: 05/03/2026
"""

import sqlite3
import math
import time
from pyproj import Transformer


# ============================================================ 0. Configuration & DB Connection ============================================================

DB_NAME = "urban_ai_v2.db"
conn = sqlite3.connect(DB_NAME)
conn.row_factory = sqlite3.Row  # Access columns by name
cursor = conn.cursor()

# Coordinate transformer: WGS84 -> NAD83 (EPSG:26986)
transformer = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)




# ============================================================ A. User Input Layer ============================================================


print("\nEnter New Store Details")

# Build category lookup from database
category_lookup = cursor.execute(
    "SELECT DISTINCT top_category, naics_code FROM pois WHERE top_category IS NOT NULL AND naics_code IS NOT NULL"
).fetchall()

# Latitude
try:
    lat = float(input("Enter latitude: "))
except ValueError:
    print("Error: Latitude must be a number.")
    exit()
if not (-90 <= lat <= 90):
    print("Error: Latitude must be between -90 and 90.")
    exit()

# Longitude
try:
    lon = float(input("Enter longitude: "))
except ValueError:
    print("Error: Longitude must be a number.")
    exit()
if not (-180 <= lon <= 180):
    print("Error: Longitude must be between -180 and 180.")
    exit()

# Top Category or NAICS Code
def naics_to_category(user_naics):
    results = cursor.execute(
        "SELECT DISTINCT top_category FROM pois WHERE naics_code = ?",
        (user_naics,)
    ).fetchall()
    return [r["top_category"] for r in results] if results else None

def category_to_naics(user_category):
    results = cursor.execute(
        "SELECT DISTINCT naics_code FROM pois WHERE LOWER(top_category) = LOWER(?)",
        (user_category,)
    ).fetchall()
    return [r["naics_code"] for r in results] if results else None

user_input = input("Enter NAICS code or top category: ").strip()

if user_input.isdigit():
    matched_categories = naics_to_category(user_input)
    if matched_categories:
        print(f"\nInput NAICS code: {user_input}")
        print("\nMatched top category/categories:")
        for cat in matched_categories:
            print(f" - {cat}")
        top_category = matched_categories[0]
        naics_code = user_input
    else:
        print(f"\nNo top category found for NAICS code: {user_input}")
        exit()
else:
    matched_naics = category_to_naics(user_input)
    if matched_naics:
        print(f"\nInput top category: {user_input}")
        print("\nMatched NAICS code(s):")
        for code in matched_naics:
            print(f" - {code}")
        top_category = user_input
        naics_code = matched_naics[0]
    else:
        print(f"\nNo NAICS code found for top category: {user_input}")
        exit()

# Store size
try:
    new_store_size = float(input("\nEnter store size (square meters): "))
except ValueError:
    print("Error: Store size must be a number.")
    exit()
if new_store_size <= 0:
    print("Error: Store size must be greater than 0.")
    exit()



# Start timing
start_time = time.time()


# ============================================================ B. Fetch Parameters from Database ============================================================

param_row = cursor.execute(
    "SELECT alpha, beta FROM calibrated_parameters WHERE naics_code = ?",
    (str(naics_code),)
).fetchone()

if param_row is None:
    print(f"Error: No calibrated parameters found for NAICS code {naics_code}")
    conn.close()
    exit()

alpha = param_row["alpha"]
beta = param_row["beta"]


# ============================================================ C. Distance Calculation ============================================================

# Fetch all CBG centroids with pre-computed projected coordinates
cbg_rows = cursor.execute(
    "SELECT GEOID10, centroid_lat, centroid_lon, proj_x, proj_y FROM cbg_master"
).fetchall()

# Project the new store coordinates
new_x, new_y = transformer.transform(lon, lat)

# Calculate distance and new store utility for each CBG
cbg_results = []
for row in cbg_rows:
    dx = row["proj_x"] - new_x
    dy = row["proj_y"] - new_y
    dist = math.sqrt(dx * dx + dy * dy)

    if dist <= 0:
        continue

    utility_new = (new_store_size ** alpha) / (dist ** beta)

    cbg_results.append({
        "GEOID10": row["GEOID10"],
        "new_store_distance_m": dist,
        "utility_new": utility_new
    })


# ============================================================ D. Huff Model Logic  ============================================================

# Fetch pre-computed existing competitor utility for this category
utility_rows = cursor.execute(
    "SELECT GEOID10, total_existing_utility FROM Competitor_Summary WHERE naics_code = ?",
    (str(naics_code),)
).fetchall()

existing_utility_map = {r["GEOID10"]: r["total_existing_utility"] for r in utility_rows}

# Fetch pre-computed demand for this category
demand_rows = cursor.execute(
    "SELECT GEOID10, total_demand FROM precomputed_demand WHERE naics_code = ?",
    (str(naics_code),)
).fetchall()

demand_map = {r["GEOID10"]: r["total_demand"] for r in demand_rows}


# ============================================================ E. Demand Estimation ============================================================

total_predicted_visits = 0.0
num_competitors = cursor.execute(
    "SELECT COUNT(*) as cnt FROM pois WHERE naics_code = ?",
    (str(naics_code),)
).fetchone()["cnt"]

total_demand_sum = 0.0

for cbg in cbg_results:
    geoid = cbg["GEOID10"]
    utility_new = cbg["utility_new"]
    existing_utility = existing_utility_map.get(geoid, 0.0)
    demand = demand_map.get(geoid, 0.0)

    # Huff probability
    p_new = utility_new / (utility_new + existing_utility) if (utility_new + existing_utility) > 0 else 0

    predicted = p_new * demand
    total_predicted_visits += predicted
    total_demand_sum += demand


# ============================================================ F. Output: Summary of Results ============================================================

elapsed = time.time() - start_time

print("\n" + "-" * 28 + "Summary of New Store Site Prediction Result:" + "-" * 28)

print("\nParameters:")
print(f"  Top Category = {top_category}")
print(f"  Category (NAICS): {naics_code}")
print(f"  Alpha: {alpha}")
print(f"  Beta: {beta}")

print(f"\nNumber of competitors: {num_competitors}")

print(f"\nTotal category demand: {total_demand_sum:.0f}")

print(f"\nTotal predicted visits to new store: {total_predicted_visits:.2f}")

print(f"\nExecution time: {elapsed:.4f} seconds")

print("\n" + "-" * 100)

conn.close()


"""
#Latitude & Longitude: (e.g., 42.27, -71.80)
#Category: (Top Category or NAICS Code e.g. 445310)
#Store Size: (Square Meters e.g. 2500)

Total predicted visits to new store: 36.77 

Execution time: 0.0010 seconds

"""