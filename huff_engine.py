"""
Huff Model Engine — ALSDS Baseline Version

Estimates predicted visits to a hypothetical new retail location using the
Huff Gravity Model.

Given a candidate store's location, NAICS category, and floor area, the model:
1. Finds existing competing POIs in the same NAICS category.
2. Computes attractiveness of existing competitors.
3. Computes attractiveness of the proposed candidate store.
4. Estimates the probability that consumers from each CBG visit the candidate.
5. Aggregates predicted visits across Worcester CBGs.

Spatial reference:
- Candidate input coordinates are expected in WGS84 (EPSG:4326).
- CBG geometries are projected to UTM Zone 19N (EPSG:26919) for distance calculations in meters.

Study area:
- Worcester, MA

Important:
- Teams may replace the internals of this file.
- However, they should keep the run_huff_model(...) function signature and return structure.
"""

import math
import sqlite3
import time
from pathlib import Path

from pyproj import Transformer

# --------------------------------------------------------------------- Configuration ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "Data" / "urban_ai_v2.db"

# WGS84 latitude/longitude -> UTM Zone 19N, meters
# This matches the projection used when building the database.
TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)



# --------------------------------------------------------------------- Database helpers ---------------------------------------------------------------------


def get_connection(db_connection=None):
    """
    Use an existing database connection if provided.
    Otherwise, open the local SQLite database from Data/urban_ai_v2.db.
    """
    if db_connection is not None:
        db_connection.row_factory = sqlite3.Row
        return db_connection, False

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"SQLite database not found at {DB_PATH}. "
            "Make sure Data/urban_ai_v2.db exists in the repository."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn, True


def resolve_naics(cursor, business_category):
    """
    Resolve user input into a NAICS code.

    The UI may pass:
    - an exact NAICS code, such as 445310
    - a top_category name
    - sometimes a shorter NAICS prefix, such as 4441

    The function tries exact NAICS first, then category name, then NAICS prefix.
    """
    user_input = str(business_category).strip()

    if not user_input:
        raise ValueError("Business category / NAICS code cannot be empty.")

    # 1. Exact NAICS match in calibrated_parameters
    row = cursor.execute(
        """
        SELECT naics_code, top_category
        FROM calibrated_parameters
        WHERE naics_code = ?
        """,
        (user_input,)
    ).fetchone()

    if row:
        return str(row["naics_code"]), row["top_category"]

    # 2. Exact top_category match
    row = cursor.execute(
        """
        SELECT naics_code, top_category
        FROM calibrated_parameters
        WHERE LOWER(top_category) = LOWER(?)
        ORDER BY naics_code
        LIMIT 1
        """,
        (user_input,)
    ).fetchone()

    if row:
        return str(row["naics_code"]), row["top_category"]

    # 3. NAICS prefix match, useful if UI sends 4-digit category code
    if user_input.isdigit():
        row = cursor.execute(
            """
            SELECT naics_code, top_category
            FROM calibrated_parameters
            WHERE naics_code LIKE ?
            ORDER BY naics_code
            LIMIT 1
            """,
            (user_input + "%",)
        ).fetchone()

        if row:
            return str(row["naics_code"]), row["top_category"]

    raise ValueError(
        f"No calibrated NAICS category found for input: {business_category}. "
        "Try an exact NAICS code or a valid top category from the database."
    )


def get_parameters(cursor, naics_code):
    """
    Fetch calibrated alpha and beta parameters for the selected NAICS code.
    """
    row = cursor.execute(
        """
        SELECT alpha, beta, top_category
        FROM calibrated_parameters
        WHERE naics_code = ?
        """,
        (str(naics_code),)
    ).fetchone()

    if row is None:
        raise ValueError(
            f"No calibrated alpha/beta parameters found for NAICS code {naics_code}."
        )

    return float(row["alpha"]), float(row["beta"]), row["top_category"]


def get_competitor_sample(cursor, naics_code, candidate_lat, candidate_lon, alpha, beta, limit=20):
    """
    Return a lightweight competitor sample for frontend map/table display.
    """
    rows = cursor.execute(
        """
        SELECT
            placekey,
            location_name,
            latitude,
            longitude,
            wkt_area_sq_meters
        FROM pois
        WHERE naics_code = ?
          AND latitude IS NOT NULL
          AND longitude IS NOT NULL
        LIMIT ?
        """,
        (str(naics_code), int(limit))
    ).fetchall()

    competitors = []

    for row in rows:
        lat = safe_float(row["latitude"])
        lon = safe_float(row["longitude"])
        size = safe_float(row["wkt_area_sq_meters"])

        distance_miles = None
        attraction = None

        if lat is not None and lon is not None:
            distance_miles = haversine_miles(candidate_lat, candidate_lon, lat, lon)

        if size is not None and distance_miles is not None:
            # Convert miles back to meters for attraction proxy.
            distance_m = max(distance_miles * 1609.344, 100.0)
            attraction = (size ** alpha) / (distance_m ** beta)

        competitors.append({
            "name": str(row["location_name"] or "Unknown"),
            "placekey": str(row["placekey"] or ""),
            "lat": lat,
            "lon": lon,
            "size": size,
            "distance_miles": round(distance_miles, 3) if distance_miles is not None else None,
            "attraction": round(attraction, 8) if attraction is not None else None,
        })

    return competitors



# --------------------------------------------------------------------- Core Huff computation ---------------------------------------------------------------------


def run_huff_model(
    candidate_lat,
    candidate_lon,
    business_category,
    floor_area,
    db_connection=None,
):
    """
    Required app-facing function.

    The Flask app calls this function directly.

    Parameters
    ----------
    candidate_lat : float
        Candidate store latitude.
    candidate_lon : float
        Candidate store longitude.
    business_category : str or int
        NAICS code or top category.
    floor_area : float
        Candidate store floor area in square meters.
    db_connection : optional
        Optional existing database connection.

    Returns
    -------
    dict
        Structured result used by the dashboard and chatbot.
    """
    start_time = time.perf_counter()

    candidate_lat = float(candidate_lat)
    candidate_lon = float(candidate_lon)
    floor_area = float(floor_area)

    if not (-90 <= candidate_lat <= 90):
        raise ValueError("candidate_lat must be between -90 and 90.")

    if not (-180 <= candidate_lon <= 180):
        raise ValueError("candidate_lon must be between -180 and 180.")

    if floor_area <= 0:
        raise ValueError("floor_area must be greater than zero.")

    conn, should_close = get_connection(db_connection)
    cursor = conn.cursor()

    try:
        # Resolve category / NAICS and fetch model parameters.
        naics_code, resolved_top_category = resolve_naics(cursor, business_category)
        alpha, beta, top_category_from_params = get_parameters(cursor, naics_code)
        top_category = resolved_top_category or top_category_from_params

        # Project candidate store coordinates.
        new_x, new_y = TRANSFORMER.transform(candidate_lon, candidate_lat)

        # Fetch all CBG centroids with projected coordinates.
        cbg_rows = cursor.execute(
            """
            SELECT GEOID10, proj_x, proj_y
            FROM cbg_master
            """
        ).fetchall()

        if not cbg_rows:
            raise ValueError("No CBG records found in cbg_master table.")

        # Existing competitor utility, precomputed by CBG and NAICS.
        utility_rows = cursor.execute(
            """
            SELECT GEOID10, total_existing_utility
            FROM Competitor_Summary
            WHERE naics_code = ?
            """,
            (str(naics_code),)
        ).fetchall()

        if not utility_rows:
            raise ValueError(
                f"No precomputed competitor utility found for NAICS code {naics_code}."
            )

        existing_utility_map = {
            str(row["GEOID10"]): float(row["total_existing_utility"] or 0.0)
            for row in utility_rows
        }

        # Category demand, precomputed by CBG and NAICS.
        demand_rows = cursor.execute(
            """
            SELECT GEOID10, total_demand
            FROM precomputed_demand
            WHERE naics_code = ?
            """,
            (str(naics_code),)
        ).fetchall()

        if not demand_rows:
            raise ValueError(
                f"No precomputed demand found for NAICS code {naics_code}."
            )

        demand_map = {
            str(row["GEOID10"]): float(row["total_demand"] or 0.0)
            for row in demand_rows
        }

        # Count competitors in this NAICS category.
        competitor_count_row = cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM pois
            WHERE naics_code = ?
            """,
            (str(naics_code),)
        ).fetchone()

        num_competitors = int(competitor_count_row["cnt"] or 0)

        if num_competitors == 0:
            raise ValueError(f"No competing POIs found for NAICS code {naics_code}.")

        # Huff demand estimation.
        total_predicted_visits = 0.0
        total_demand_sum = 0.0

        for row in cbg_rows:
            geoid = str(row["GEOID10"])

            demand = demand_map.get(geoid, 0.0)
            if demand <= 0:
                continue

            existing_utility = existing_utility_map.get(geoid, 0.0)

            dx = float(row["proj_x"]) - new_x
            dy = float(row["proj_y"]) - new_y
            distance_m = math.sqrt(dx * dx + dy * dy)

            # Avoid division instability if candidate is extremely close to a centroid.
            distance_m = max(distance_m, 100.0)

            # Candidate utility: Uij = Aj^alpha / dij^beta
            utility_new = (floor_area ** alpha) / (distance_m ** beta)

            denominator = utility_new + existing_utility
            p_new = utility_new / denominator if denominator > 0 else 0.0

            predicted = p_new * demand

            total_predicted_visits += predicted
            total_demand_sum += demand

        market_share = (
            total_predicted_visits / total_demand_sum
            if total_demand_sum > 0
            else 0.0
        )

        competitors = get_competitor_sample(
            cursor=cursor,
            naics_code=naics_code,
            candidate_lat=candidate_lat,
            candidate_lon=candidate_lon,
            alpha=alpha,
            beta=beta,
            limit=20,
        )

        runtime_ms = round((time.perf_counter() - start_time) * 1000, 2)

        return {
            "predicted_visits": round(total_predicted_visits, 2),
            "market_share": round(market_share, 6),
            "competitors": competitors,
            "runtime_ms": runtime_ms,
            "notes": (
                "Team 4 V3 Huff model completed successfully. "
                "This version reads from the local SQLite database at Data/urban_ai_v2.db "
                "instead of loading CSV or GeoJSON files. "
                "It uses precomputed CBG coordinates, competitor utility, and category demand "
                "to improve integration and runtime efficiency."
            ),
            "inputs": {
                "candidate_lat": candidate_lat,
                "candidate_lon": candidate_lon,
                "business_category": str(business_category),
                "resolved_naics_code": str(naics_code),
                "resolved_top_category": str(top_category),
                "floor_area": floor_area,
                "alpha": alpha,
                "beta": beta,
                "competitor_count": num_competitors,
                "total_category_demand": round(total_demand_sum, 2),
            },
        }

    finally:
        if should_close:
            conn.close()

# --------------------------------------------------------------------- Utility helpers ---------------------------------------------------------------------


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def haversine_miles(lat1, lon1, lat2, lon2):
    """
    Approximate great-circle distance in miles.
    Used only for frontend competitor display.
    Core Huff distances use projected coordinates in meters.
    """
    radius_miles = 3958.7613

    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_miles * c


# Local quick test:
# result = run_huff_model(
#     candidate_lat=42.27,
#     candidate_lon=-71.80,
#     business_category=445310,
#     floor_area=2500,
# )
# print(result)