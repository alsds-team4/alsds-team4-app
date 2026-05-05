import os
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
                "error": "Invalid input type. Latitude, longitude, and floor area must be numeric. NAICS/business category must be provided."
            }), 400

        if not business_category:
            return jsonify({"ok": False, "error": "Business category / NAICS code cannot be empty."}), 400

        if candidate_lat < -90 or candidate_lat > 90:
            return jsonify({"ok": False, "error": "candidate_lat must be between -90 and 90."}), 400

        if candidate_lon < -180 or candidate_lon > 180:
            return jsonify({"ok": False, "error": "candidate_lon must be between -180 and 180."}), 400

        if floor_area <= 0:
            return jsonify({"ok": False, "error": "floor_area must be greater than zero."}), 400

        result = run_huff_model(
            candidate_lat=candidate_lat,
            candidate_lon=candidate_lon,
            business_category=business_category,
            floor_area=floor_area,
            db_connection=None  # Teams can replace this with Azure SQL usage
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
        return jsonify({"ok": False, "error": str(e)}), 500


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
