import os
import threading
from flask import Flask, jsonify, request, render_template, abort
from openai import AzureOpenAI
from db import test_connection

app = Flask(__name__)

def migration_endpoints_enabled():
    """
    Migration endpoints were useful during development, but they should not
    remain active in the final deployed app.

    To enable temporarily, set this Azure App Service environment variable:
    ENABLE_MIGRATION_ENDPOINTS=true
    """
    return os.getenv("ENABLE_MIGRATION_ENDPOINTS", "false").lower() == "true"


# -------------------------
# Azure OpenAI Setup
# -------------------------

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
)

DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")


# -------------------------
# Routes
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

        with get_connection() as conn:
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


        try:
            candidate_lat = float(candidate_lat)
            candidate_lon = float(candidate_lon)
            floor_area = float(floor_area)
            business_category = str(business_category).strip()
        except Exception:
            return jsonify({
                "ok": False,
                "error": "Invalid input type. Latitude, longitude, and floor area must be numeric. NAICS/business category must be provided."
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

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ----------------------- List categories ---------------------------------


@app.route("/api/categories", methods=["GET"])
def api_categories():
    """
    Return supported store types and NAICS codes from Azure SQL.

    We query calibrated_parameters because these are the categories
    the Huff Model can actually run with.
    """
    try:
        from db import get_connection

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT
                CAST([naics_code] AS NVARCHAR(50)) AS [naics_code],
                COALESCE(
                    NULLIF(LTRIM(RTRIM(CAST([top_category] AS NVARCHAR(255)))), ''),
                    CAST([naics_code] AS NVARCHAR(50))
                ) AS [store_type]
            FROM dbo.[calibrated_parameters]
            WHERE [naics_code] IS NOT NULL
            ORDER BY [store_type], [naics_code]
        """)

        rows = cursor.fetchall()

        categories = []
        for row in rows:
            categories.append({
                "naics_code": str(row[0]),
                "store_type": str(row[1])
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
            return jsonify({"ok": False, "error": "Missing question or result"}), 400

        answer = answer_question(question, result)

        return jsonify({"ok": True, "answer": answer})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


# -------------------------
# LLM Functions
# -------------------------

def generate_explanation(result):
    prompt = f"""
You are an expert in retail location analytics.

A Huff-style gravity model has been run with the following results:

Predicted visits: {result.get("predicted_visits")}
Market share: {result.get("market_share")}
Runtime (ms): {result.get("runtime_ms")}

Competitors (sample):
{safe_competitor_sample(result, 3)}

Explain clearly:
1. What the predicted visits and market share mean
2. What factors likely influenced the result
3. Keep it short and intuitive, about 3-5 sentences
"""

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


def answer_question(question, result):
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


# -------------------------
# Run locally
# -------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)




