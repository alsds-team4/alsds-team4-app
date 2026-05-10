"""
migration_script_v2.py
Migrates CSV and GeoJSON data into a local SQLite3 database (urban_ai.db).

Requirements:
- Combines worcester_cbgs.csv demographics with GeoJSON spatial coordinates
- Pre-computes projected X/Y coordinates (EPSG:26986)
- Pre-computes competitor utility sums by category for each CBG
- Uses parameterized queries throughout

Team members: Wilson ChungNam Wan, Omotolani Orekoya, Sharelle Allen, Rojisha Awale
Date: 05/03/2026
"""

import sqlite3
import pandas as pd
import json
import math
from pyproj import Transformer


# ============================================================ 0. Configuration ============================================================

DB_NAME = "urban_ai_v2.db"

CSV_CBGS = "worcester_cbgs.csv"
CSV_POIS = "worcester_pois.csv"
CSV_VISITS = "worcester_cbg_poi_visits.csv"
CSV_DISTANCE = "worcester_cbg_poi_distance.csv"
CSV_PARAMS = "calibrated_parameters_filtered.csv"
GEOJSON_FILE = "worcester_cbgs_map.geojson"

# Coordinate transformer: WGS84 -> NAD83 / Massachusetts Mainland (meters)
"""transformer = Transformer.from_crs("EPSG:4326", "EPSG:26986", always_xy=True)"""
transformer = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)

# ============================================================ 1. Load source files ============================================================

print("Loading source files...")

cbgs_df = pd.read_csv(CSV_CBGS)
pois_df = pd.read_csv(CSV_POIS)
visits_df = pd.read_csv(CSV_VISITS)
distance_df = pd.read_csv(CSV_DISTANCE)
params_df = pd.read_csv(CSV_PARAMS)

with open(GEOJSON_FILE, "r", encoding="utf-8") as f:
    geojson = json.load(f)

print(f"  cbgs:     {len(cbgs_df)} rows")
print(f"  pois:     {len(pois_df)} rows")
print(f"  visits:   {len(visits_df)} rows")
print(f"  distance: {len(distance_df)} rows")
print(f"  params:   {len(params_df)} rows")
print(f"  geojson:  {len(geojson['features'])} features")


# ============================================================ 2. Build CBG Master Table (demographics + spatial) ============================================================

print("\nBuilding CBG master table...")

# Extract GEOID10 and centroid lat/lon from GeoJSON
geo_records = []
for feature in geojson["features"]:
    props = feature["properties"]
    geo_records.append({
        "GEOID10": str(props["GEOID10"]),
        "centroid_lat": float(props["INTPTLAT10"]),
        "centroid_lon": float(props["INTPTLON10"])
    })

geo_df = pd.DataFrame(geo_records)

# The worcester_cbgs.csv lost GEOID precision (Excel scientific notation).
# Both files have 149 rows in matching order, so we combine by index.
cbg_master = pd.concat([geo_df, cbgs_df.drop(columns=["cbg"])], axis=1)

# Pre-compute projected X and Y coordinates (EPSG:26919)
def project_coords(lat, lon):
    x, y = transformer.transform(lon, lat)
    return x, y

cbg_master["proj_x"], cbg_master["proj_y"] = zip(
    *cbg_master.apply(lambda r: project_coords(r["centroid_lat"], r["centroid_lon"]), axis=1)
)

print(f"  CBG master table: {len(cbg_master)} rows with projected coordinates")


# ============================================================ 3. Normalize POIs table (separate category lookup) ============================================================

print("Normalizing POI data...")

pois_df["naics_code"] = pois_df["naics_code"].astype(str).str.strip()
pois_df["top_category"] = pois_df["top_category"].astype(str).str.strip()

# Category parameters table (already clean)
params_df["NAICS code"] = params_df["NAICS code"].astype(str).str.strip()
params_df["top_category"] = params_df["top_category"].astype(str).str.strip()


# ============================================================ 4. Pre-compute Competitor Utility by CBG and NAICS code ============================================================

print("Pre-computing competitor utility sums (this may take a moment)...")

# Ensure correct types
distance_df["GEOID10"] = distance_df["GEOID10"].astype(str)
distance_df["distance_m"] = pd.to_numeric(distance_df["distance_m"], errors="coerce")

pois_for_utility = pois_df[["placekey", "naics_code", "wkt_area_sq_meters"]].copy()
pois_for_utility["wkt_area_sq_meters"] = pd.to_numeric(
    pois_for_utility["wkt_area_sq_meters"], errors="coerce"
)

# Merge distance with POI info
dist_with_poi = distance_df.merge(pois_for_utility, on="placekey", how="inner")
dist_with_poi = dist_with_poi.dropna(subset=["distance_m", "wkt_area_sq_meters"])
dist_with_poi = dist_with_poi[dist_with_poi["distance_m"] > 0]

# For each NAICS code that has calibrated parameters, compute utility
precomputed_rows = []

for _, param_row in params_df.iterrows():
    naics = str(param_row["NAICS code"])
    alpha = param_row["alpha"]
    beta = param_row["beta"]

    # Filter to this category
    cat_data = dist_with_poi[dist_with_poi["naics_code"] == naics].copy()

    if len(cat_data) == 0:
        continue

    # Utility = size^alpha / distance^beta
    cat_data["utility"] = (
        cat_data["wkt_area_sq_meters"] ** alpha
    ) / (
        cat_data["distance_m"] ** beta
    )

    # Sum utility by CBG
    utility_sum = cat_data.groupby("GEOID10", as_index=False)["utility"].sum()

    for _, row in utility_sum.iterrows():
        precomputed_rows.append({
            "GEOID10": row["GEOID10"],
            "naics_code": naics,
            "total_existing_utility": row["utility"]
        })

precomputed_df = pd.DataFrame(precomputed_rows)
print(f"  Pre-computed utility: {len(precomputed_df)} (CBG x category) combinations")


# ============================================================ 5. Pre-compute total category demand by CBG and NAICS ============================================================

print("Pre-computing category demand by CBG...")

visits_df["visitor_home_cbg"] = visits_df["visitor_home_cbg"].astype(str)
visits_df["placekey"] = visits_df["placekey"].astype(str)

visits_with_cat = visits_df.merge(
    pois_df[["placekey", "naics_code"]],
    on="placekey",
    how="left"
)

demand_by_cbg_cat = visits_with_cat.groupby(
    ["visitor_home_cbg", "naics_code"], as_index=False
)["visit_count"].sum()

demand_by_cbg_cat = demand_by_cbg_cat.rename(columns={
    "visitor_home_cbg": "GEOID10",
    "visit_count": "total_demand"
})

print(f"  Demand records: {len(demand_by_cbg_cat)}")


# ============================================================ 6. Create SQLite Database and Tables ============================================================

print(f"\nCreating database: {DB_NAME}")

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

# Drop existing tables for clean rebuild
cursor.executescript("""
    DROP TABLE IF EXISTS cbg_master;
    DROP TABLE IF EXISTS pois;
    DROP TABLE IF EXISTS visits;
    DROP TABLE IF EXISTS distance_matrix;
    DROP TABLE IF EXISTS calibrated_parameters;
    DROP TABLE IF EXISTS precomputed_utility;
    DROP TABLE IF EXISTS Competitor_Summary;
    DROP TABLE IF EXISTS precomputed_demand;
""")

# --- CBG Master Table ---
cursor.execute("""
    CREATE TABLE cbg_master (
        GEOID10 TEXT PRIMARY KEY,
        centroid_lat REAL NOT NULL,
        centroid_lon REAL NOT NULL,
        proj_x REAL NOT NULL,
        proj_y REAL NOT NULL,
        total_population INTEGER,
        median_household_income REAL,
        median_age REAL,
        white_population REAL,
        black_population REAL,
        asian_population REAL,
        hispanic_population REAL,
        uni_degree REAL,
        income_q TEXT,
        education_q TEXT,
        age_q TEXT
    );
""")

# --- POIs Table ---
cursor.execute("""
    CREATE TABLE pois (
        placekey TEXT PRIMARY KEY,
        location_name TEXT,
        brands TEXT,
        top_category TEXT,
        sub_category TEXT,
        naics_code TEXT,
        latitude REAL,
        longitude REAL,
        poi_cbg TEXT,
        wkt_area_sq_meters REAL
    );
""")

# --- Visits Table ---
cursor.execute("""
    CREATE TABLE visits (
        visitor_home_cbg TEXT,
        placekey TEXT,
        visit_count INTEGER
    );
""")

# --- Distance Matrix ---
cursor.execute("""
    CREATE TABLE distance_matrix (
        placekey TEXT,
        GEOID10 TEXT,
        distance_m REAL
    );
""")

# --- Calibrated Parameters ---
cursor.execute("""
    CREATE TABLE calibrated_parameters (
        top_category TEXT,
        naics_code TEXT PRIMARY KEY,
        alpha REAL,
        beta REAL,
        correlation REAL
    );
""")

# --- Competitor Utility ---
cursor.execute("""
    CREATE TABLE precomputed_utility (
        GEOID10 TEXT,
        naics_code TEXT,
        total_existing_utility REAL,
        PRIMARY KEY (GEOID10, naics_code)
    );
""")

# --- Competitor_Summary ---
cursor.execute("""
    CREATE TABLE Competitor_Summary (
        GEOID10 TEXT,
        naics_code TEXT,
        total_existing_utility REAL,
        PRIMARY KEY (GEOID10, naics_code)
    );
""")


# --- Pre-computed Category Demand ---
cursor.execute("""
    CREATE TABLE precomputed_demand (
        GEOID10 TEXT,
        naics_code TEXT,
        total_demand INTEGER,
        PRIMARY KEY (GEOID10, naics_code)
    );
""")


# ============================================================ 7. Insert Data with Parameterized Queries ============================================================

print("Inserting data...")

# CBG Master
cbg_rows = cbg_master.to_dict("records")
cursor.executemany("""
    INSERT INTO cbg_master (
        GEOID10, centroid_lat, centroid_lon, proj_x, proj_y,
        total_population, median_household_income, median_age,
        white_population, black_population, asian_population, hispanic_population,
        uni_degree, income_q, education_q, age_q
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", [
    (r["GEOID10"], r["centroid_lat"], r["centroid_lon"], r["proj_x"], r["proj_y"],
     r["total_population"], r["median_household_income"], r["median_age"],
     r["white_population"], r["black_population"], r["asian_population"],
     r["hispanic_population"], r["uni_degree"], r["income_q"], r["education_q"], r["age_q"])
    for r in cbg_rows
])
print(f"  cbg_master: {len(cbg_rows)} rows")

# POIs (only keep columns needed for the engine)
pois_insert = pois_df[["placekey", "location_name", "brands", "top_category",
                         "sub_category", "naics_code", "latitude", "longitude",
                         "poi_cbg", "wkt_area_sq_meters"]].copy()
pois_insert["poi_cbg"] = pois_insert["poi_cbg"].astype(str)
pois_rows = pois_insert.where(pois_insert.notna(), None).to_dict("records")
cursor.executemany("""
    INSERT INTO pois (placekey, location_name, brands, top_category, sub_category,
                      naics_code, latitude, longitude, poi_cbg, wkt_area_sq_meters)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", [
    (r["placekey"], r["location_name"], r["brands"], r["top_category"],
     r["sub_category"], r["naics_code"], r["latitude"], r["longitude"],
     r["poi_cbg"], r["wkt_area_sq_meters"])
    for r in pois_rows
])
print(f"  pois: {len(pois_rows)} rows")

# Visits
visits_rows = visits_df.to_dict("records")
cursor.executemany("""
    INSERT INTO visits (visitor_home_cbg, placekey, visit_count)
    VALUES (?, ?, ?)
""", [(str(r["visitor_home_cbg"]), str(r["placekey"]), r["visit_count"]) for r in visits_rows])
print(f"  visits: {len(visits_rows)} rows")

# Distance Matrix
dist_rows = distance_df.to_dict("records")
cursor.executemany("""
    INSERT INTO distance_matrix (placekey, GEOID10, distance_m)
    VALUES (?, ?, ?)
""", [(str(r["placekey"]), str(r["GEOID10"]), r["distance_m"]) for r in dist_rows])
print(f"  distance_matrix: {len(dist_rows)} rows")

# Calibrated Parameters
params_rows = params_df.to_dict("records")
cursor.executemany("""
    INSERT INTO calibrated_parameters (top_category, naics_code, alpha, beta, correlation)
    VALUES (?, ?, ?, ?, ?)
""", [(r["top_category"], str(r["NAICS code"]), r["alpha"], r["beta"], r["correlation"]) for r in params_rows])
print(f"  calibrated_parameters: {len(params_rows)} rows")

# Pre-computed Utility
util_rows = precomputed_df.to_dict("records")
cursor.executemany("""
    INSERT INTO precomputed_utility (GEOID10, naics_code, total_existing_utility)
    VALUES (?, ?, ?)
""", [(r["GEOID10"], r["naics_code"], r["total_existing_utility"]) for r in util_rows])
print(f"  precomputed_utility: {len(util_rows)} rows")

# Competitor_Summary
cursor.executemany("""
    INSERT INTO Competitor_Summary (GEOID10, naics_code, total_existing_utility)
    VALUES (?, ?, ?)
""", [(r["GEOID10"], r["naics_code"], r["total_existing_utility"]) for r in util_rows])
print(f"  Competitor_Summary: {len(util_rows)} rows")

# Pre-computed Demand
demand_rows = demand_by_cbg_cat.to_dict("records")
cursor.executemany("""
    INSERT INTO precomputed_demand (GEOID10, naics_code, total_demand)
    VALUES (?, ?, ?)
""", [(str(r["GEOID10"]), str(r["naics_code"]), int(r["total_demand"])) for r in demand_rows])
print(f"  precomputed_demand: {len(demand_rows)} rows")


# ============================================================ 8. Create Indexes for Query Optimization ============================================================

print("\nCreating indexes...")

cursor.executescript("""
    CREATE INDEX IF NOT EXISTS idx_pois_naics ON pois(naics_code);
    CREATE INDEX IF NOT EXISTS idx_visits_cbg ON visits(visitor_home_cbg);
    CREATE INDEX IF NOT EXISTS idx_visits_placekey ON visits(placekey);
    CREATE INDEX IF NOT EXISTS idx_distance_placekey ON distance_matrix(placekey);
    CREATE INDEX IF NOT EXISTS idx_distance_geoid ON distance_matrix(GEOID10);

    CREATE INDEX IF NOT EXISTS idx_utility_naics ON precomputed_utility(naics_code);
    CREATE INDEX IF NOT EXISTS idx_utility_naics_geoid ON precomputed_utility(naics_code, GEOID10);

    CREATE INDEX IF NOT EXISTS idx_competitor_summary_naics ON Competitor_Summary(naics_code);
    CREATE INDEX IF NOT EXISTS idx_competitor_summary_naics_geoid ON Competitor_Summary(naics_code, GEOID10);

    CREATE INDEX IF NOT EXISTS idx_demand_naics ON precomputed_demand(naics_code);
    CREATE INDEX IF NOT EXISTS idx_demand_naics_geoid ON precomputed_demand(naics_code, GEOID10);
""")


# ============================================================ 9. Check ============================================================

conn.commit()

print("\n=== Migration Complete ===")
print(f"Database: {DB_NAME}\n")

tables = cursor.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()

for (table_name,) in tables:
    count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"  {table_name}: {count} rows")

conn.close()
print(f"\nDone! Open {DB_NAME} in DB Browser for SQLite to inspect.")