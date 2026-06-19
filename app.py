import os
import threading

from flask import Flask, jsonify, request, render_template, abort
from openai import AzureOpenAI

from db import test_connection


app = Flask(__name__)


# -------------------------
# Migration Endpoint Control
# -------------------------

def migration_endpoints_enabled():
    """
    Migration endpoints were useful during development, but they should not
    remain active in the final deployed app.

    To temporarily enable them, set this Azure App Service environment variable:
    ENABLE_MIGRATION_ENDPOINTS=true
    """
    return os.getenv("ENABLE_MIGRATION_ENDPOINTS", "false").lower() == "true"


# -------------------------
# Azure OpenAI Setup
# -------------------------

def get_openai_client():
    """
    Create Azure OpenAI client only when needed.

    This avoids crashing the whole Flask app at startup if Azure OpenAI
    environment variables are missing or temporarily misconfigured.
    """
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    if not api_key or not api_version or not azure_endpoint:
        return None

    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=azure_endpoint,
    )


DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")


# -------------------------
# Main Routes
# -------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/dbcheck")
def dbcheck():
    try:
        ok = test_connection()
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# -------------------------
# Azure SQL Migration Admin Routes
# -------------------------

@app.route("/admin/migrate")
def admin_migrate():
    """
    Starts the Azure SQL migration in a background thread.

    This endpoint is disabled by default in the final deployed app.
    """
    if not migration_endpoints_enabled():
        abort(404)

    from migrate_to_azure_sql import execute_migration_task, migration_status

    if migration_status.get("is_running") is True:
        return jsonify({
            "ok": False,
            "message": "Migration is already running.",
            "current_progress": migration_status
        }), 202

    thread = threading.Thread(target=execute_migration_task)
    thread.daemon = True
    thread.start()

    return jsonify({
        "ok": True,
        "message": "Migration initialized successfully in the background.",
        "check_status_url": "/admin/migrate/status"
    }), 202


@app.route("/admin/migrate/status")
def admin_migrate_status():
    """
    Shows current migration progress.

    This endpoint is disabled by default in the final deployed app.
    """
    if not migration_endpoints_enabled():
        abort(404)

    from migrate_to_azure_sql import migration_status
    return jsonify(migration_status)


@app.route("/db_structure")
def db_structure():
    """
    Shows Azure SQL table names and row counts.
    """
    conn = None

    try:
        from db import get_connection

        query = """
            SELECT
                t.name AS TABLE_NAME,
                SUM(p.rows) AS row_count
            FROM
                sys.tables t
            INNER JOIN
                sys.indexes i ON t.object_id = i.object_id
            INNER JOIN
                sys.partitions p ON i.object_id = p.object_id
                    AND i.index_id = p.index_id
            WHERE
                t.is_ms_shipped = 0
                AND i.index_id IN (0, 1)
            GROUP BY
                t.name
            ORDER BY
                t.name;
        """

        structure_report = []

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()

        for row in rows:
            structure_report.append({
                "TABLE_NAME": str(row[0]),
                "row_count": int(row[1])
            })

        return jsonify(structure_report)

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# -------------------------
# Run Huff Model
# -------------------------

@app.route("/api/run_huff", methods=["POST"])
def api_run_huff():
    try:
        from huff_engine import run_huff_model

        data = request.get_json(silent=True) or {}

        candidate_lat = get_first_present(data, ["candidate_lat", "lat", "latitude"])
        candidate_lon = get_first_present(data, ["candidate_lon", "lon", "lng", "longitude"])
        business_category = get_first_present(data, ["business_category", "naics_code", "naics"])
        floor_area = get_first_present(data, ["floor_area", "floor_area_sqm", "area", "area_sqm"])

        missing = []

        if candidate_lat is None:
            missing.append("candidate_lat")

        if candidate_lon is None:
            missing.append("candidate_lon")

        if business_category is None:
            missing.append("business_category or naics_code")

        if floor_area is None:
            missing.append("floor_area or floor_area_sqm")

        if missing:
            return jsonify({
                "ok": False,
                "error": "Missing required inputs: " + ", ".join(missing)
            }), 400

        try:
            candidate_lat = float(candidate_lat)
            candidate_lon = float(candidate_lon)
            floor_area = float(floor_area)
            business_category = str(business_category).strip()
        except Exception:
            return jsonify({
                "ok": False,
                "error": (
                    "Invalid input type. Latitude, longitude, and floor area must be numeric. "
                    "NAICS/business category must be provided."
                )
            }), 400

        if not business_category:
            return jsonify({
                "ok": False,
                "error": "Business category / NAICS code cannot be empty."
            }), 400

        if candidate_lat < -90 or candidate_lat > 90:
            return jsonify({
                "ok": False,
                "error": "candidate_lat must be between -90 and 90."
            }), 400

        if candidate_lon < -180 or candidate_lon > 180:
            return jsonify({
                "ok": False,
                "error": "candidate_lon must be between -180 and 180."
            }), 400

        if floor_area <= 0:
            return jsonify({
                "ok": False,
                "error": "floor_area must be greater than zero."
            }), 400

        result = run_huff_model(
            candidate_lat=candidate_lat,
            candidate_lon=candidate_lon,
            business_category=business_category,
            floor_area=floor_area,
            db_connection=None
        )

        explanation = generate_explanation(result)

        return jsonify({
            "ok": True,
            "inputs": {
                "candidate_lat": candidate_lat,
                "candidate_lon": candidate_lon,
                "business_category": business_category,
                "floor_area": floor_area
            },
            "result": result,
            "explanation": explanation
        })

    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# -------------------------
# List Supported Categories
# -------------------------

@app.route("/api/categories", methods=["GET"])
def api_categories():
    """
    Return NAICS codes available in POI data.

    Codes that exist in calibrated_parameters are marked as calibrated.
    Codes that only exist in POI data are marked as fallback_default.
    """
    conn = None

    try:
        from db import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                CAST(p.[naics_code] AS NVARCHAR(50)) AS [naics_code],
                COALESCE(
                    MAX(NULLIF(LTRIM(RTRIM(CAST(cp.[top_category] AS NVARCHAR(255)))), '')),
                    MAX(NULLIF(LTRIM(RTRIM(CAST(p.[top_category] AS NVARCHAR(255)))), '')),
                    MAX(NULLIF(LTRIM(RTRIM(CAST(p.[sub_category] AS NVARCHAR(255)))), '')),
                    CAST(p.[naics_code] AS NVARCHAR(50))
                ) AS [store_type],
                CASE
                    WHEN MAX(CASE WHEN cp.[naics_code] IS NOT NULL THEN 1 ELSE 0 END) = 1
                    THEN 'calibrated'
                    ELSE 'fallback_default'
                END AS [parameter_status]
            FROM dbo.[pois] p
            LEFT JOIN dbo.[calibrated_parameters] cp
                ON CAST(p.[naics_code] AS NVARCHAR(50)) = CAST(cp.[naics_code] AS NVARCHAR(50))
            WHERE p.[naics_code] IS NOT NULL
            GROUP BY CAST(p.[naics_code] AS NVARCHAR(50))
            ORDER BY [store_type], [naics_code]
        """)

        rows = cursor.fetchall()

        categories = []

        for row in rows:
            categories.append({
                "naics_code": str(row[0]),
                "store_type": str(row[1]),
                "parameter_status": str(row[2])
            })

        return jsonify({
            "ok": True,
            "count": len(categories),
            "categories": categories
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# -------------------------
# Ask Follow-up Questions
# -------------------------

@app.route("/api/ask", methods=["POST"])
def api_ask():
    try:
        data = request.get_json(silent=True) or {}
        question = data.get("question")
        result = data.get("result")

        if not question or not result:
            return jsonify({
                "ok": False,
                "error": "Missing question or result"
            }), 400

        answer = answer_question(question, result)

        return jsonify({
            "ok": True,
            "answer": answer
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# -------------------------
# Helper Functions
# -------------------------

def get_first_present(data, keys):
    """
    Returns the first value found in a dictionary from a list of possible keys.

    This lets the frontend send either:
      business_category / floor_area
    or:
      naics_code / floor_area_sqm
    """
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)

    return None


def safe_competitor_sample(result, n=3):
    competitors = result.get("competitors", [])

    if not isinstance(competitors, list):
        return []

    return competitors[:n]


def fallback_explanation(result):
    """
    Fallback explanation if Azure OpenAI is not configured or unavailable.
    """
    predicted_visits = result.get("predicted_visits")
    market_share = result.get("market_share")
    inputs = result.get("inputs", {})
    source = inputs.get("parameter_source", "unknown")

    return (
        f"The model estimates about {predicted_visits} predicted visits and a market share of {market_share}. "
        f"The result is based on the selected location, store size, nearby competitors, and demand records. "
        f"The parameter source used for this run was {source}. "
        "Review the competitor table and saved scenario comparison to decide whether this location is stronger than alternatives."
    )


# -------------------------
# LLM Functions
# -------------------------

def generate_explanation(result):
    client = get_openai_client()

    if client is None or not DEPLOYMENT:
        return fallback_explanation(result)

    prompt = f"""
You are an expert in retail location analytics.

A Huff-style gravity model has been run with the following results:

Predicted visits: {result.get("predicted_visits")}
Market share: {result.get("market_share")}
Runtime (ms): {result.get("runtime_ms")}

Model inputs:
{result.get("inputs")}

Competitors (sample):
{safe_competitor_sample(result, 3)}

Explain clearly:
1. What the predicted visits and market share mean
2. What factors likely influenced the result
3. Keep it short and intuitive, about 3-5 sentences
"""

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": "You explain retail analytics and Huff model results clearly for students."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.4
        )

        return response.choices[0].message.content

    except Exception:
        return fallback_explanation(result)


def answer_question(question, result):
    client = get_openai_client()

    if client is None or not DEPLOYMENT:
        return (
            "The AI explanation service is not available right now. "
            "However, you can still review the model output, competitor table, and saved scenario comparison. "
            "The result should be interpreted based on predicted visits, market share, competitor count, and parameter source."
        )

    prompt = f"""
You are assisting with a retail location analysis using a Huff model.

Model result:
{result}

User question:
{question}

Answer clearly and concisely, grounded in the model output.

Important rules:
- Do not invent data.
- Do not claim that you reran the Huff model.
- If the user asks to rerun the model with new inputs, explain that the app can rerun the model when the message includes all required inputs: NAICS code, floor area, latitude, and longitude.
"""

    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful data science assistant for a location analytics web app."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.5
        )

        return response.choices[0].message.content

    except Exception as e:
        return (
            "The AI explanation service could not answer this follow-up question. "
            f"Error: {str(e)}"
        )


# -------------------------
# Run Locally
# -------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
