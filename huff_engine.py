"""
Huff Model Engine — Azure SQL Version

This version preserves the required run_huff_model(...) function signature,
but queries Azure SQL instead of opening the local SQLite database.

Required signature:
run_huff_model(candidate_lat, candidate_lon, business_category, floor_area, db_connection=None)
"""

import math
import time

from pyproj import Transformer
from db import get_connection as get_azure_connection


# WGS84 latitude/longitude -> UTM Zone 19N, meters
# This matches the projection used when building the original SQLite database.
TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)


# ---------------------------------------------------------------------
# Row 
# ---------------------------------------------------------------------

def row_value(row, index, default=None):
    """
    Safely read pyodbc.Row values by index.
    pyodbc rows are not the same as sqlite3.Row, so we avoid row["column_name"].
    """
    try:
        value = row[index]
        return default if value is None else value
    except Exception:
        return default


# ---------------------------------------------------------------------
# Database 
# ---------------------------------------------------------------------

def open_model_connection(db_connection=None):
    """
    Use an existing database connection if provided.
    Otherwise, open Azure SQL connection through db.py.

    db.py is responsible for reading SQL_CONNECTION_STRING from Azure App Service.
    """
    if db_connection is not None:
        return db_connection, False

    conn = get_azure_connection()
    return conn, True


# ---------------------------------------------------------------------
# NAICS / parameter
# ---------------------------------------------------------------------

def resolve_naics(cursor, business_category):
    """
    Resolve user input into a NAICS code.

    The UI may pass:
    - exact NAICS code, e.g. 445310
    - top_category name
    - shorter NAICS prefix, e.g. 4441

    This uses Azure SQL / T-SQL syntax.
    """
    user_input = str(business_category).strip()

    if not user_input:
        raise ValueError("Business category / NAICS code cannot be empty.")

    # 1. Exact NAICS match
    cursor.execute(
        """
        SELECT [naics_code], [top_category]
        FROM dbo.[calibrated_parameters]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
        """,
        user_input
    )
    row = cursor.fetchone()

    if row:
        return str(row_value(row, 0)), row_value(row, 1)

    # 2. Exact top_category match
    cursor.execute(
        """
        SELECT TOP 1 [naics_code], [top_category]
        FROM dbo.[calibrated_parameters]
        WHERE LOWER([top_category]) = LOWER(?)
        ORDER BY [naics_code]
        """,
        user_input
    )
    row = cursor.fetchone()

    if row:
        return str(row_value(row, 0)), row_value(row, 1)

    # 3. NAICS prefix match
    if user_input.isdigit():
        cursor.execute(
            """
            SELECT TOP 1 [naics_code], [top_category]
            FROM dbo.[calibrated_parameters]
            WHERE CAST([naics_code] AS NVARCHAR(50)) LIKE ?
            ORDER BY [naics_code]
            """,
            user_input + "%"
        )
        row = cursor.fetchone()

        if row:
            return str(row_value(row, 0)), row_value(row, 1)

    raise ValueError(
        f"No calibrated NAICS category found for input: {business_category}. "
        "Try an exact NAICS code or a valid top category from the database."
    )


def get_parameters(cursor, naics_code):
    """
    Fetch calibrated alpha and beta parameters for the selected NAICS code.
    """
    cursor.execute(
        """
        SELECT [alpha], [beta], [top_category]
        FROM dbo.[calibrated_parameters]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
        """,
        str(naics_code)
    )
    row = cursor.fetchone()

    if row is None:
        raise ValueError(
            f"No calibrated alpha/beta parameters found for NAICS code {naics_code}."
        )

    alpha = float(row_value(row, 0))
    beta = float(row_value(row, 1))
    top_category = row_value(row, 2)

    return alpha, beta, top_category


def get_competitor_sample(cursor, naics_code, candidate_lat, candidate_lon, alpha, beta, limit=20):
    """
    Return a lightweight competitor sample for frontend map/table display.
    """
    safe_limit = max(1, min(int(limit), 100))

    cursor.execute(
        f"""
        SELECT TOP {safe_limit}
            [placekey],
            [location_name],
            [latitude],
            [longitude],
            [wkt_area_sq_meters]
        FROM dbo.[pois]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
          AND [latitude] IS NOT NULL
          AND [longitude] IS NOT NULL
        """,
        str(naics_code)
    )
    rows = cursor.fetchall()

    competitors = []

    for row in rows:
        placekey = row_value(row, 0, "")
        location_name = row_value(row, 1, "Unknown")
        lat = safe_float(row_value(row, 2))
        lon = safe_float(row_value(row, 3))
        size = safe_float(row_value(row, 4))

        distance_miles = None
        attraction = None

        if lat is not None and lon is not None:
            distance_miles = haversine_miles(candidate_lat, candidate_lon, lat, lon)

        if size is not None and distance_miles is not None:
            distance_m = max(distance_miles * 1609.344, 100.0)
            attraction = (size ** alpha) / (distance_m ** beta)

        competitors.append({
            "name": str(location_name or "Unknown"),
            "placekey": str(placekey or ""),
            "lat": lat,
            "lon": lon,
            "size": size,
            "distance_miles": round(distance_miles, 3) if distance_miles is not None else None,
            "attraction": round(attraction, 8) if attraction is not None else None,
        })

    return competitors


# ---------------------------------------------------------------------
# Huff computation
# ---------------------------------------------------------------------

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
        Optional existing Azure SQL database connection.

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

    conn, should_close = open_model_connection(db_connection)
    cursor = conn.cursor()

    try:
        # Resolve category / NAICS and fetch model parameters.
        naics_code, resolved_top_category = resolve_naics(cursor, business_category)
        alpha, beta, top_category_from_params = get_parameters(cursor, naics_code)
        top_category = resolved_top_category or top_category_from_params

        # Project candidate store coordinates.
        new_x, new_y = TRANSFORMER.transform(candidate_lon, candidate_lat)

        # Fetch all CBG centroids with projected coordinates.
        cursor.execute(
            """
            SELECT [GEOID10], [proj_x], [proj_y]
            FROM dbo.[cbg_master]
            """
        )
        cbg_rows = cursor.fetchall()

        if not cbg_rows:
            raise ValueError("No CBG records found in cbg_master table.")

        # Existing competitor utility, precomputed by CBG and NAICS.
        cursor.execute(
            """
            SELECT [GEOID10], [total_existing_utility]
            FROM dbo.[Competitor_Summary]
            WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
            """,
            str(naics_code)
        )
        utility_rows = cursor.fetchall()

        if not utility_rows:
            raise ValueError(
                f"No precomputed competitor utility found for NAICS code {naics_code}."
            )

        existing_utility_map = {
            str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
            for row in utility_rows
        }

        # Category demand, precomputed by CBG and NAICS.
        cursor.execute(
            """
            SELECT [GEOID10], [total_demand]
            FROM dbo.[precomputed_demand]
            WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
            """,
            str(naics_code)
        )
        demand_rows = cursor.fetchall()

        if not demand_rows:
            raise ValueError(
                f"No precomputed demand found for NAICS code {naics_code}."
            )

        demand_map = {
            str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
            for row in demand_rows
        }

        # Count competitors in this NAICS category.
        cursor.execute(
            """
            SELECT COUNT(*) AS [cnt]
            FROM dbo.[pois]
            WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
            """,
            str(naics_code)
        )
        competitor_count_row = cursor.fetchone()
        num_competitors = int(row_value(competitor_count_row, 0, 0) or 0)

        if num_competitors == 0:
            raise ValueError(f"No competing POIs found for NAICS code {naics_code}.")

        # Huff demand estimation.
        total_predicted_visits = 0.0
        total_demand_sum = 0.0

        for row in cbg_rows:
            geoid = str(row_value(row, 0))
            proj_x = safe_float(row_value(row, 1))
            proj_y = safe_float(row_value(row, 2))

            if proj_x is None or proj_y is None:
                continue

            demand = demand_map.get(geoid, 0.0)

            if demand <= 0:
                continue

            existing_utility = existing_utility_map.get(geoid, 0.0)

            dx = proj_x - new_x
            dy = proj_y - new_y
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
                "This version reads from Azure SQL instead of the local SQLite database. "
                "It uses precomputed CBG coordinates, competitor utility, and category demand "
                "to improve integration and runtime efficiency."
            ),
            "data_source": "Azure SQL",
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


# ---------------------------------------------------------------------
# Utility 
# ---------------------------------------------------------------------

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














