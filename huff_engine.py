import math
import time

from pyproj import Transformer
from db import get_connection as get_azure_connection


# WGS84 latitude/longitude -> UTM Zone 19N, meters.
# This matches the projection used when building the original SQLite database.
TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:26919", always_xy=True)

DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 2.0

NO_HISTORY_NAICS_MESSAGE = (
    "There are no historical records for this NAICS code / business category in our data, "
    "and therefore the model cannot produce results for this NAICS code. "
    "Please try another NAICS code / business category."
)


# ---------------------------------------------------------------------
# Row helpers
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


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def pick_column(columns, candidates):
    """
    Pick the first matching column from a table, case-insensitively.
    Used for defensive fallback logic when the visits table schema varies.
    """
    lookup = {str(col).lower(): str(col) for col in columns}

    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]

    return None


def get_table_columns(cursor, table_name):
    """
    Return column names for a fixed table name.
    table_name should only be a trusted internal table name.
    """
    cursor.execute(f"SELECT TOP 0 * FROM dbo.[{table_name}]")
    return [column[0] for column in cursor.description]


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
# NAICS code and parameter handling
# ---------------------------------------------------------------------

def resolve_naics_and_parameters(cursor, business_category):
    """
    Resolve user input into a NAICS code and Huff Model parameters.

    Case 1:
    If the NAICS code exists in calibrated_parameters, use calibrated alpha/beta.

    Case 2:
    If the NAICS code exists in POI data but not in calibrated_parameters,
    use fallback alpha=1 and beta=2.

    Case 3:
    If the NAICS code does not exist in POI data, raise a clear ValueError.
    """

    user_input = str(business_category or "").strip()

    if not user_input:
        raise ValueError(
            "Please enter a NAICS code or business category, such as 445310 or liquor store."
        )

    normalized = " ".join(user_input.lower().split())
    is_naics_code = user_input.isdigit()

    if is_naics_code:
        # Case 1: exact NAICS exists in calibrated_parameters.
        cursor.execute(
            """
            SELECT TOP 1
                CAST([naics_code] AS NVARCHAR(50)) AS [naics_code],
                COALESCE(
                    NULLIF(LTRIM(RTRIM(CAST([top_category] AS NVARCHAR(255)))), ''),
                    CAST([naics_code] AS NVARCHAR(50))
                ) AS [store_type],
                [alpha],
                [beta]
            FROM dbo.[calibrated_parameters]
            WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
            """,
            user_input,
        )

        row = cursor.fetchone()

        if row:
            return {
                "naics_code": str(row_value(row, 0)),
                "store_type": str(row_value(row, 1)),
                "alpha": float(row_value(row, 2)),
                "beta": float(row_value(row, 3)),
                "parameter_source": "calibrated_parameters",
            }

        # Case 2 / Case 3: not calibrated, so check POI data.
        cursor.execute(
            """
            SELECT TOP 1
                CAST([naics_code] AS NVARCHAR(50)) AS [naics_code],
                COALESCE(
                    NULLIF(LTRIM(RTRIM(CAST([top_category] AS NVARCHAR(255)))), ''),
                    NULLIF(LTRIM(RTRIM(CAST([sub_category] AS NVARCHAR(255)))), ''),
                    NULLIF(LTRIM(RTRIM(CAST([location_name] AS NVARCHAR(255)))), ''),
                    CAST([naics_code] AS NVARCHAR(50))
                ) AS [store_type]
            FROM dbo.[pois]
            WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
            """,
            user_input,
        )

        poi_row = cursor.fetchone()

        if poi_row:
            return {
                "naics_code": str(row_value(poi_row, 0)),
                "store_type": str(row_value(poi_row, 1)),
                "alpha": DEFAULT_ALPHA,
                "beta": DEFAULT_BETA,
                "parameter_source": "fallback_default",
            }

        raise ValueError(NO_HISTORY_NAICS_MESSAGE)

    # Store-type text input, such as "liquor store" or "grocery".
    # First try calibrated_parameters.
    cursor.execute(
        """
        SELECT TOP 1
            CAST([naics_code] AS NVARCHAR(50)) AS [naics_code],
            COALESCE(
                NULLIF(LTRIM(RTRIM(CAST([top_category] AS NVARCHAR(255)))), ''),
                CAST([naics_code] AS NVARCHAR(50))
            ) AS [store_type],
            [alpha],
            [beta]
        FROM dbo.[calibrated_parameters]
        WHERE LOWER(CAST([top_category] AS NVARCHAR(255))) LIKE ?
        ORDER BY [naics_code]
        """,
        f"%{normalized}%",
    )

    row = cursor.fetchone()

    if row:
        return {
            "naics_code": str(row_value(row, 0)),
            "store_type": str(row_value(row, 1)),
            "alpha": float(row_value(row, 2)),
            "beta": float(row_value(row, 3)),
            "parameter_source": "calibrated_parameters",
        }

    # Store-type text fallback: search POI data.
    # If the matched NAICS has calibrated parameters, use them.
    # Otherwise use alpha=1, beta=2.
    cursor.execute(
        """
        SELECT TOP 1
            CAST(p.[naics_code] AS NVARCHAR(50)) AS [naics_code],
            COALESCE(
                MAX(NULLIF(LTRIM(RTRIM(CAST(cp.[top_category] AS NVARCHAR(255)))), '')),
                MAX(NULLIF(LTRIM(RTRIM(CAST(p.[top_category] AS NVARCHAR(255)))), '')),
                MAX(NULLIF(LTRIM(RTRIM(CAST(p.[sub_category] AS NVARCHAR(255)))), '')),
                CAST(p.[naics_code] AS NVARCHAR(50))
            ) AS [store_type],
            MAX(cp.[alpha]) AS [alpha],
            MAX(cp.[beta]) AS [beta],
            CASE
                WHEN MAX(CASE WHEN cp.[naics_code] IS NOT NULL THEN 1 ELSE 0 END) = 1
                THEN 'calibrated_parameters'
                ELSE 'fallback_default'
            END AS [parameter_source],
            COUNT(*) AS [match_count]
        FROM dbo.[pois] p
        LEFT JOIN dbo.[calibrated_parameters] cp
            ON CAST(p.[naics_code] AS NVARCHAR(50)) = CAST(cp.[naics_code] AS NVARCHAR(50))
        WHERE
            LOWER(CAST(p.[top_category] AS NVARCHAR(255))) LIKE ?
            OR LOWER(CAST(p.[sub_category] AS NVARCHAR(255))) LIKE ?
            OR LOWER(CAST(p.[location_name] AS NVARCHAR(255))) LIKE ?
        GROUP BY CAST(p.[naics_code] AS NVARCHAR(50))
        ORDER BY
            CASE
                WHEN MAX(CASE WHEN cp.[naics_code] IS NOT NULL THEN 1 ELSE 0 END) = 1
                THEN 0
                ELSE 1
            END,
            COUNT(*) DESC
        """,
        f"%{normalized}%",
        f"%{normalized}%",
        f"%{normalized}%",
    )

    poi_match = cursor.fetchone()

    if poi_match:
        alpha = row_value(poi_match, 2)
        beta = row_value(poi_match, 3)
        parameter_source = str(row_value(poi_match, 4))

        return {
            "naics_code": str(row_value(poi_match, 0)),
            "store_type": str(row_value(poi_match, 1)),
            "alpha": float(alpha) if alpha is not None else DEFAULT_ALPHA,
            "beta": float(beta) if beta is not None else DEFAULT_BETA,
            "parameter_source": parameter_source,
        }

    raise ValueError(NO_HISTORY_NAICS_MESSAGE)


# Backward-compatible wrappers in case another part of the app imports them.
def resolve_naics(cursor, business_category):
    resolved = resolve_naics_and_parameters(cursor, business_category)
    return resolved["naics_code"], resolved["store_type"]


def get_parameters(cursor, naics_code):
    resolved = resolve_naics_and_parameters(cursor, naics_code)
    return resolved["alpha"], resolved["beta"], resolved["store_type"]


# ---------------------------------------------------------------------
# Demand and utility helpers
# ---------------------------------------------------------------------

def fetch_precomputed_existing_utility(cursor, naics_code):
    cursor.execute(
        """
        SELECT [GEOID10], [total_existing_utility]
        FROM dbo.[Competitor_Summary]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
        """,
        str(naics_code),
    )
    rows = cursor.fetchall()

    return {
        str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
        for row in rows
    }


def compute_existing_utility_from_pois(cursor, naics_code, cbg_rows, alpha, beta):
    """
    Fallback utility calculation for NAICS codes that exist in POI data
    but do not have precomputed competitor utility records.
    """
    cursor.execute(
        """
        SELECT
            [latitude],
            [longitude],
            [wkt_area_sq_meters]
        FROM dbo.[pois]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
          AND [latitude] IS NOT NULL
          AND [longitude] IS NOT NULL
        """,
        str(naics_code),
    )

    poi_rows = cursor.fetchall()
    competitors = []

    for row in poi_rows:
        lat = safe_float(row_value(row, 0))
        lon = safe_float(row_value(row, 1))
        size = safe_float(row_value(row, 2))

        if lat is None or lon is None:
            continue

        # Use a small positive fallback if size is missing, so the POI can still
        # contribute to competition instead of being silently ignored.
        safe_size = max(size if size is not None else 100.0, 1.0)
        x, y = TRANSFORMER.transform(lon, lat)

        competitors.append({
            "x": x,
            "y": y,
            "size": safe_size,
        })

    if not competitors:
        return {}

    utility_map = {}

    for row in cbg_rows:
        geoid = str(row_value(row, 0))
        proj_x = safe_float(row_value(row, 1))
        proj_y = safe_float(row_value(row, 2))

        if proj_x is None or proj_y is None:
            continue

        total_utility = 0.0

        for competitor in competitors:
            dx = proj_x - competitor["x"]
            dy = proj_y - competitor["y"]
            distance_m = math.sqrt(dx * dx + dy * dy)
            distance_m = max(distance_m, 100.0)

            total_utility += (competitor["size"] ** alpha) / (distance_m ** beta)

        utility_map[geoid] = total_utility

    return utility_map


def fetch_existing_utility_map(cursor, naics_code, cbg_rows, alpha, beta):
    """
    Prefer precomputed competitor utility.
    If it does not exist, compute competitor utility from POI data.
    """
    precomputed_map = fetch_precomputed_existing_utility(cursor, naics_code)

    if precomputed_map:
        return precomputed_map, "Competitor_Summary"

    dynamic_map = compute_existing_utility_from_pois(
        cursor=cursor,
        naics_code=naics_code,
        cbg_rows=cbg_rows,
        alpha=alpha,
        beta=beta,
    )

    if dynamic_map:
        return dynamic_map, "dynamic_poi_fallback"

    raise ValueError(f"No competing POIs found for NAICS code {naics_code}.")


def fetch_precomputed_demand(cursor, naics_code):
    cursor.execute(
        """
        SELECT [GEOID10], [total_demand]
        FROM dbo.[precomputed_demand]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
        """,
        str(naics_code),
    )
    rows = cursor.fetchall()

    return {
        str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
        for row in rows
    }


def fetch_demand_from_visits(cursor, naics_code):
    """
    Try to calculate category demand from the visits table if useful columns exist.

    This is defensive because different course versions may have slightly
    different visits table column names.
    """
    try:
        columns = get_table_columns(cursor, "visits")
    except Exception:
        return {}

    placekey_col = pick_column(columns, [
        "placekey",
        "poi_placekey",
        "safegraph_placekey",
    ])

    geoid_col = pick_column(columns, [
        "GEOID10",
        "visitor_home_cbg",
        "home_cbg",
        "cbg",
        "visitor_cbg",
        "origin_cbg",
    ])

    visits_col = pick_column(columns, [
        "raw_visit_counts",
        "visits",
        "visit_count",
        "total_visits",
        "visits_count",
    ])

    if not placekey_col or not geoid_col or not visits_col:
        return {}

    query = f"""
        SELECT
            CAST(v.[{geoid_col}] AS NVARCHAR(50)) AS [GEOID10],
            SUM(COALESCE(TRY_CAST(v.[{visits_col}] AS FLOAT), 0)) AS [total_demand]
        FROM dbo.[visits] v
        INNER JOIN dbo.[pois] p
            ON CAST(v.[{placekey_col}] AS NVARCHAR(255)) = CAST(p.[placekey] AS NVARCHAR(255))
        WHERE CAST(p.[naics_code] AS NVARCHAR(50)) = ?
        GROUP BY CAST(v.[{geoid_col}] AS NVARCHAR(50))
    """

    try:
        cursor.execute(query, str(naics_code))
        rows = cursor.fetchall()
    except Exception:
        return {}

    return {
        str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
        for row in rows
    }


def fetch_generic_demand(cursor):
    """
    Last-resort fallback demand source.

    This uses all available historical demand from precomputed_demand by CBG.
    It is used only when there is no category-level precomputed demand and
    no usable category-level visits aggregation.
    """
    try:
        cursor.execute(
            """
            SELECT [GEOID10], SUM([total_demand]) AS [total_demand]
            FROM dbo.[precomputed_demand]
            GROUP BY [GEOID10]
            """
        )
        rows = cursor.fetchall()
    except Exception:
        return {}

    return {
        str(row_value(row, 0)): float(row_value(row, 1, 0.0) or 0.0)
        for row in rows
    }


def fetch_demand_map(cursor, naics_code):
    """
    Prefer category-specific precomputed demand.
    If unavailable, try visits-based category demand.
    If still unavailable, use generic historical demand by CBG.
    """
    precomputed_map = fetch_precomputed_demand(cursor, naics_code)

    if precomputed_map:
        return precomputed_map, "precomputed_demand"

    visits_map = fetch_demand_from_visits(cursor, naics_code)

    if visits_map:
        return visits_map, "visits_category_fallback"

    generic_map = fetch_generic_demand(cursor)

    if generic_map:
        return generic_map, "generic_demand_fallback"

    raise ValueError("No usable demand records found for model calculation.")


def get_competitor_count(cursor, naics_code):
    cursor.execute(
        """
        SELECT COUNT(*) AS [cnt]
        FROM dbo.[pois]
        WHERE CAST([naics_code] AS NVARCHAR(50)) = ?
        """,
        str(naics_code),
    )
    row = cursor.fetchone()
    return int(row_value(row, 0, 0) or 0)


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
        str(naics_code),
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

    conn = None
    should_close = False

    try:
        conn, should_close = open_model_connection(db_connection)
        cursor = conn.cursor()

        resolved = resolve_naics_and_parameters(cursor, business_category)

        resolved_naics_code = resolved["naics_code"]
        resolved_store_type = resolved["store_type"]
        alpha = resolved["alpha"]
        beta = resolved["beta"]
        parameter_source = resolved["parameter_source"]

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

        existing_utility_map, utility_source = fetch_existing_utility_map(
            cursor=cursor,
            naics_code=resolved_naics_code,
            cbg_rows=cbg_rows,
            alpha=alpha,
            beta=beta,
        )

        demand_map, demand_source = fetch_demand_map(
            cursor=cursor,
            naics_code=resolved_naics_code,
        )

        num_competitors = get_competitor_count(cursor, resolved_naics_code)

        if num_competitors == 0:
            raise ValueError(f"No competing POIs found for NAICS code {resolved_naics_code}.")

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
            naics_code=resolved_naics_code,
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
                f"Resolved NAICS code: {resolved_naics_code}. "
                f"Resolved store type: {resolved_store_type}. "
                f"Parameter source: {parameter_source}. "
                f"Alpha: {alpha}, Beta: {beta}. "
                f"Competitor utility source: {utility_source}. "
                f"Demand source: {demand_source}."
            ),
            "data_source": "Azure SQL",
            "inputs": {
                "candidate_lat": candidate_lat,
                "candidate_lon": candidate_lon,
                "requested_business_category": str(business_category),
                "resolved_naics_code": str(resolved_naics_code),
                "resolved_store_type": str(resolved_store_type),
                "floor_area": floor_area,
                "alpha": alpha,
                "beta": beta,
                "parameter_source": parameter_source,
                "competitor_utility_source": utility_source,
                "demand_source": demand_source,
                "competitor_count": num_competitors,
                "total_category_demand": round(total_demand_sum, 2),
            },
        }

    finally:
        if should_close and conn is not None:
            conn.close()


# ---------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------

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
