import os
import threading
from flask import Flask, request, jsonify, render_template
from openai import AzureOpenAI

from db import test_connection

app = Flask(__name__)


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
    Required by Module 7.
    """
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
    Required by Module 7.
    """
    from migrate_to_azure_sql import migration_status

    return jsonify(migration_status)


@app.route("/db_structure")
def db_structure():
    """
    Shows Azure SQL table names and row counts.
    Required by Module 7.
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




