

import math
import sqlite3
import zipfile
from pathlib import Path

from db import get_connection


# ============================================================
# 0. Global migration status
# ============================================================

migration_status = {
    "status": "idle",
    "is_running": False,
    "source_database": None,
    "migrated_tables": {},
    "indexing": "Pending",
    "error": None
}


# ============================================================
# 1. Configuration
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "Data"

# Your SQLite database name
DB_FILENAME = "urban_ai_v2.db"

# Preferred locations
DB_PATH = DATA_DIR / DB_FILENAME
ZIP_PATH = DATA_DIR / f"{DB_FILENAME}.zip"
ROOT_DB_PATH = BASE_DIR / DB_FILENAME


# These are the tables created by your original migration_v2.py
# and required by your Huff engine logic.
TABLES_TO_COPY = {
    "cbg_master": [
        "GEOID10",
        "centroid_lat",
        "centroid_lon",
        "proj_x",
        "proj_y",
        "total_population",
        "median_household_income",
        "median_age",
        "white_population",
        "black_population",
        "asian_population",
        "hispanic_population",
        "uni_degree",
        "income_q",
        "education_q",
        "age_q",
    ],
    "pois": [
        "placekey",
        "location_name",
        "brands",
        "top_category",
        "sub_category",
        "naics_code",
        "latitude",
        "longitude",
        "poi_cbg",
        "wkt_area_sq_meters",
    ],
    "visits": [
        "visitor_home_cbg",
        "placekey",
        "visit_count",
    ],
    "distance_matrix": [
        "placekey",
        "GEOID10",
        "distance_m",
    ],
    "calibrated_parameters": [
        "top_category",
        "naics_code",
        "alpha",
        "beta",
        "correlation",
    ],
    "precomputed_utility": [
        "GEOID10",
        "naics_code",
        "total_existing_utility",
    ],
    "Competitor_Summary": [
        "GEOID10",
        "naics_code",
        "total_existing_utility",
    ],
    "precomputed_demand": [
        "GEOID10",
        "naics_code",
        "total_demand",
    ],
}


# ============================================================
# 2. Locate SQLite database
# ============================================================

def get_sqlite_db_path():
    """
    Locate the SQLite database inside the deployed app.

    Priority:
    1. Data/urban_ai_v2.db
    2. Data/urban_ai_v2.db.zip, then unzip it
    3. ./urban_ai_v2.db in project root
    """

    DATA_DIR.mkdir(exist_ok=True)

    if DB_PATH.exists():
        return DB_PATH

    if ZIP_PATH.exists():
        with zipfile.ZipFile(ZIP_PATH, "r") as zip_ref:
            zip_ref.extractall(DATA_DIR)

        if DB_PATH.exists():
            return DB_PATH

        db_candidates = list(DATA_DIR.glob("*.db"))
        if db_candidates:
            return db_candidates[0]

    if ROOT_DB_PATH.exists():
        return ROOT_DB_PATH

    raise FileNotFoundError(
        f"Could not find {DB_FILENAME}. Expected one of: "
        f"{DB_PATH}, {ZIP_PATH}, or {ROOT_DB_PATH}"
    )


# ============================================================
# 3. Helpers
# ============================================================

def quote_identifier(identifier):
    """
    T-SQL bracket formatting.
    Example:
    naics_code -> [naics_code]
    Competitor_Summary -> [Competitor_Summary]
    """
    safe = str(identifier).replace("]", "]]")
    return f"[{safe}]"


def clean_value(value):
    """
    Convert SQLite values into Azure SQL-safe primitive values.
    """

    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")

    return value


# ============================================================
# 4. Create Azure SQL tables
# ============================================================

def create_azure_tables(cursor):
    """
    Drop and recreate Azure SQL tables.
    This makes /admin/migrate safe to rerun.
    """

    # Drop child/dependent tables first
    cursor.execute("DROP TABLE IF EXISTS dbo.[precomputed_demand]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[Competitor_Summary]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[precomputed_utility]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[calibrated_parameters]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[distance_matrix]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[visits]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[pois]")
    cursor.execute("DROP TABLE IF EXISTS dbo.[cbg_master]")

    cursor.execute("""
        CREATE TABLE dbo.[cbg_master] (
            [GEOID10] NVARCHAR(50) PRIMARY KEY,
            [centroid_lat] FLOAT NOT NULL,
            [centroid_lon] FLOAT NOT NULL,
            [proj_x] FLOAT NOT NULL,
            [proj_y] FLOAT NOT NULL,
            [total_population] INT NULL,
            [median_household_income] FLOAT NULL,
            [median_age] FLOAT NULL,
            [white_population] FLOAT NULL,
            [black_population] FLOAT NULL,
            [asian_population] FLOAT NULL,
            [hispanic_population] FLOAT NULL,
            [uni_degree] FLOAT NULL,
            [income_q] NVARCHAR(50) NULL,
            [education_q] NVARCHAR(50) NULL,
            [age_q] NVARCHAR(50) NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[pois] (
            [placekey] NVARCHAR(150) PRIMARY KEY,
            [location_name] NVARCHAR(MAX) NULL,
            [brands] NVARCHAR(MAX) NULL,
            [top_category] NVARCHAR(255) NULL,
            [sub_category] NVARCHAR(255) NULL,
            [naics_code] NVARCHAR(50) NULL,
            [latitude] FLOAT NULL,
            [longitude] FLOAT NULL,
            [poi_cbg] NVARCHAR(50) NULL,
            [wkt_area_sq_meters] FLOAT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[visits] (
            [visitor_home_cbg] NVARCHAR(50) NULL,
            [placekey] NVARCHAR(150) NULL,
            [visit_count] INT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[distance_matrix] (
            [placekey] NVARCHAR(150) NULL,
            [GEOID10] NVARCHAR(50) NULL,
            [distance_m] FLOAT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[calibrated_parameters] (
            [top_category] NVARCHAR(255) NULL,
            [naics_code] NVARCHAR(50) PRIMARY KEY,
            [alpha] FLOAT NULL,
            [beta] FLOAT NULL,
            [correlation] FLOAT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[precomputed_utility] (
            [GEOID10] NVARCHAR(50) NOT NULL,
            [naics_code] NVARCHAR(50) NOT NULL,
            [total_existing_utility] FLOAT NULL,
            CONSTRAINT [PK_precomputed_utility] PRIMARY KEY ([GEOID10], [naics_code])
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[Competitor_Summary] (
            [GEOID10] NVARCHAR(50) NOT NULL,
            [naics_code] NVARCHAR(50) NOT NULL,
            [total_existing_utility] FLOAT NULL,
            CONSTRAINT [PK_Competitor_Summary] PRIMARY KEY ([GEOID10], [naics_code])
        )
    """)

    cursor.execute("""
        CREATE TABLE dbo.[precomputed_demand] (
            [GEOID10] NVARCHAR(50) NOT NULL,
            [naics_code] NVARCHAR(50) NOT NULL,
            [total_demand] INT NULL,
            CONSTRAINT [PK_precomputed_demand] PRIMARY KEY ([GEOID10], [naics_code])
        )
    """)


# ============================================================
# 5. Create indexes
# ============================================================

def create_indexes(cursor):
    """
    Create indexes for faster Huff engine queries.
    Since tables are dropped and recreated each migration,
    index names should not conflict.
    """

    index_statements = [
        "CREATE INDEX [idx_pois_naics] ON dbo.[pois]([naics_code])",
        "CREATE INDEX [idx_visits_cbg] ON dbo.[visits]([visitor_home_cbg])",
        "CREATE INDEX [idx_visits_placekey] ON dbo.[visits]([placekey])",
        "CREATE INDEX [idx_distance_placekey] ON dbo.[distance_matrix]([placekey])",
        "CREATE INDEX [idx_distance_geoid] ON dbo.[distance_matrix]([GEOID10])",

        "CREATE INDEX [idx_utility_naics] ON dbo.[precomputed_utility]([naics_code])",
        "CREATE INDEX [idx_utility_naics_geoid] ON dbo.[precomputed_utility]([naics_code], [GEOID10])",

        "CREATE INDEX [idx_competitor_summary_naics] ON dbo.[Competitor_Summary]([naics_code])",
        "CREATE INDEX [idx_competitor_summary_naics_geoid] ON dbo.[Competitor_Summary]([naics_code], [GEOID10])",

        "CREATE INDEX [idx_demand_naics] ON dbo.[precomputed_demand]([naics_code])",
        "CREATE INDEX [idx_demand_naics_geoid] ON dbo.[precomputed_demand]([naics_code], [GEOID10])",
    ]

    for statement in index_statements:
        cursor.execute(statement)


# ============================================================
# 6. Copy SQLite table into Azure SQL
# ============================================================

def copy_table(sqlite_cursor, azure_cursor, azure_conn, table_name, columns, batch_size=25000):
    """
    Copy one SQLite table into the matching Azure SQL table.
    Data is streamed in batches to avoid memory problems.
    """

    migration_status["migrated_tables"][table_name] = {
        "status": "processing",
        "rows": 0
    }

    sqlite_columns = ", ".join(f'"{col}"' for col in columns)
    azure_columns = ", ".join(quote_identifier(col) for col in columns)
    placeholders = ", ".join(["?"] * len(columns))

    sqlite_query = f'SELECT {sqlite_columns} FROM "{table_name}"'

    insert_query = f"""
        INSERT INTO dbo.{quote_identifier(table_name)} ({azure_columns})
        VALUES ({placeholders})
    """

    sqlite_cursor.execute(sqlite_query)
    azure_cursor.fast_executemany = True

    total_rows = 0

    while True:
        rows = sqlite_cursor.fetchmany(batch_size)

        if not rows:
            break

        cleaned_rows = [
            tuple(clean_value(value) for value in row)
            for row in rows
        ]

        azure_cursor.executemany(insert_query, cleaned_rows)
        azure_conn.commit()

        total_rows += len(cleaned_rows)

        migration_status["migrated_tables"][table_name] = {
            "status": "processing",
            "rows": total_rows
        }

    migration_status["migrated_tables"][table_name] = {
        "status": "success",
        "rows": total_rows
    }


# ============================================================
# 7. Verification helper
# ============================================================

def verify_azure_tables(cursor):
    """
    Return row counts from Azure SQL after migration.
    """

    report = {}

    for table_name in TABLES_TO_COPY.keys():
        cursor.execute(f"SELECT COUNT(*) FROM dbo.{quote_identifier(table_name)}")
        count = cursor.fetchone()[0]

        report[table_name] = int(count)

    return report


# ============================================================
# 8. Background migration task
# ============================================================

def execute_migration_task():
    """
    Background worker task.

    This function is designed to be called by Flask route:
    /admin/migrate

    It should run inside the deployed Azure Web App, where db.py can access
    the preconfigured SQL_CONNECTION_STRING.
    """

    global migration_status

    migration_status["status"] = "running"
    migration_status["is_running"] = True
    migration_status["source_database"] = None
    migration_status["migrated_tables"] = {}
    migration_status["indexing"] = "Pending"
    migration_status["error"] = None

    sqlite_conn = None
    azure_conn = None

    try:
        sqlite_db_path = get_sqlite_db_path()

        migration_status["source_database"] = str(sqlite_db_path)
        migration_status["status"] = "opening_sqlite"

        sqlite_conn = sqlite3.connect(str(sqlite_db_path))
        sqlite_cursor = sqlite_conn.cursor()

        migration_status["status"] = "connecting_azure_sql"

        # This reuses db.py, as required by the professor's explanation.
        azure_conn = get_connection()
        azure_cursor = azure_conn.cursor()

        migration_status["status"] = "creating_tables"

        create_azure_tables(azure_cursor)
        azure_conn.commit()

        migration_status["status"] = "copying_data"

        for table_name, columns in TABLES_TO_COPY.items():
            copy_table(
                sqlite_cursor=sqlite_cursor,
                azure_cursor=azure_cursor,
                azure_conn=azure_conn,
                table_name=table_name,
                columns=columns,
                batch_size=25000
            )

        migration_status["status"] = "indexing"
        migration_status["indexing"] = "Creating indexes..."

        create_indexes(azure_cursor)
        azure_conn.commit()

        migration_status["indexing"] = "Optimizations applied cleanly."

        migration_status["status"] = "verifying"

        row_counts = verify_azure_tables(azure_cursor)

        migration_status["verification"] = row_counts
        migration_status["status"] = "completed"

    except Exception as e:
        migration_status["status"] = "failed"
        migration_status["error"] = str(e)

    finally:
        migration_status["is_running"] = False

        if sqlite_conn is not None:
            sqlite_conn.close()

        if azure_conn is not None:
            try:
                azure_conn.close()
            except Exception:
                pass


# ============================================================
# 9. Optional local execution for debugging only
# ============================================================

if __name__ == "__main__":
    execute_migration_task()
    print(migration_status)
