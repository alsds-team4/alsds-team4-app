const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

const state = {
  step: "category",
  business_category: null,
  candidate_lat: null,
  candidate_lon: null,
  floor_area: null,
  last_result: null
};

addBotMessage(
  "Welcome. I will guide you through a store-location scenario for Worcester, MA. " +
  "First, enter the business NAICS code. For example: 4441."
);

sendBtn.addEventListener("click", handleSend);

chatInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    handleSend();
  }
});

window.onMapLocationSelected = function (location) {
  state.candidate_lat = location.lat;
  state.candidate_lon = location.lon;

  if (state.step === "location") {
    addBotMessage(
      `Great, I captured the candidate location: ${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}. ` +
      "Now enter the proposed store floor area in square meters."
    );
    state.step = "floor_area";
  }
};

async function handleSend() {
  const text = chatInput.value.trim();
  if (!text) return;

  addUserMessage(text);
  chatInput.value = "";

  try {
    /*
      IMPORTANT:
      Before treating the message as a normal follow-up question,
      check whether the user is asking to rerun the model with a new full set of inputs.

      Example supported message:
      "use 42.229212, -71.805525 and rerun the model for NAICS code 4441 and area of 1000 square meters"
    */
    const rerunInputs = extractRerunInputs(text);

    if (rerunInputs) {
      await rerunModelFromMessage(rerunInputs);
      return;
    }

    if (state.step === "category") {
      const naicsCode = text.trim();

      if (!/^\d+$/.test(naicsCode)) {
        addBotMessage("Please enter a numeric NAICS code. For example: 4441.");
        return;
      }

      state.business_category = naicsCode;
      state.step = "location";

      addBotMessage(
        "Good. Now click the proposed store location on the map. " +
        "You can also type coordinates as: 42.24, -71.78"
      );
      return;
    }

    if (state.step === "location") {
      const coords = parseCoordinates(text);

      if (!coords) {
        addBotMessage("Please click the map or type coordinates in this format: 42.24, -71.78");
        return;
      }

      state.candidate_lat = coords.lat;
      state.candidate_lon = coords.lon;

      if (window.setCandidateLocation) {
        window.setCandidateLocation(coords.lat, coords.lon, false);
      }

      state.step = "floor_area";
      addBotMessage("Great. Now enter the proposed store floor area in square meters.");
      return;
    }

    if (state.step === "floor_area") {
      const area = Number(text.replace(/,/g, ""));

      if (!Number.isFinite(area) || area <= 0) {
        addBotMessage("Please enter a positive numeric floor area, such as 1000.");
        return;
      }

      state.floor_area = area;
      state.step = "ready";

      addBotMessage(
        `Thanks. I will run the Huff model for NAICS ${state.business_category}, ` +
        `location (${state.candidate_lat.toFixed(6)}, ${state.candidate_lon.toFixed(6)}), ` +
        `and floor area ${state.floor_area} square meters.`
      );

      await runModel();
      return;
    }

    if (state.step === "ready") {
      await askQuestion(text);
      return;
    }
  } catch (error) {
    addErrorMessage(error.message || String(error));
  }
}

async function rerunModelFromMessage(inputs) {
  state.business_category = inputs.business_category;
  state.candidate_lat = inputs.candidate_lat;
  state.candidate_lon = inputs.candidate_lon;
  state.floor_area = inputs.floor_area;
  state.step = "ready";

  addBotMessage(
    `I found a new complete model input set. I will rerun the Huff model for NAICS ${state.business_category}, ` +
    `location (${state.candidate_lat.toFixed(6)}, ${state.candidate_lon.toFixed(6)}), ` +
    `and floor area ${state.floor_area} square meters.`
  );

  if (window.setCandidateLocation) {
    window.setCandidateLocation(state.candidate_lat, state.candidate_lon, false);
  }

  await runModel();
}

async function runModel() {
  addBotMessage("Running the model now...");

  const response = await fetch("/api/run_huff", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      candidate_lat: state.candidate_lat,
      candidate_lon: state.candidate_lon,
      business_category: state.business_category,
      floor_area: state.floor_area,

      // Optional aliases for clearer backend compatibility
      naics_code: state.business_category,
      floor_area_sqm: state.floor_area
    })
  });

  const data = await response.json();

  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Model failed.");
  }

  state.last_result = data.result;

  renderResult(data.result);

  if (window.plotCompetitors) {
    window.plotCompetitors(data.result.competitors);
  }

  addBotMessage(
    data.explanation ||
    "Model completed. You can now ask follow-up questions about the result, or provide a new NAICS code, area, and coordinates to rerun the model."
  );
}

async function askQuestion(question) {
  if (!state.last_result) {
    addBotMessage("Please complete a model run first.");
    return;
  }

  const response = await fetch("/api/ask", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      question,
      result: state.last_result
    })
  });

  const data = await response.json();

  if (!response.ok || !data.ok) {
    throw new Error(data.error || "The assistant could not answer.");
  }

  addBotMessage(data.answer);
}

function extractRerunInputs(message) {
  const coords = parseCoordinates(message);

  if (!coords) {
    return null;
  }

  const naicsMatch =
    message.match(/naics(?:\s+code)?\s*(?:is|=|:|of|for)?\s*(\d{2,6})/i) ||
    message.match(/business\s+category\s*(?:is|=|:|of|for)?\s*(\d{2,6})/i) ||
    message.match(/category\s*(?:is|=|:|of|for)?\s*(\d{2,6})/i);

  const areaMatch =
    message.match(/area\s*(?:of|is|=|:)?\s*([\d,]+(?:\.\d+)?)/i) ||
    message.match(/floor\s+area\s*(?:of|is|=|:)?\s*([\d,]+(?:\.\d+)?)/i) ||
    message.match(/([\d,]+(?:\.\d+)?)\s*(?:square\s+meters|square\s+metres|sqm|sq\.?\s*m|m2|m²)/i);

  if (!naicsMatch || !areaMatch) {
    return null;
  }

  const businessCategory = naicsMatch[1];
  const floorArea = Number(areaMatch[1].replace(/,/g, ""));

  if (!businessCategory || !Number.isFinite(floorArea) || floorArea <= 0) {
    return null;
  }

  return {
    business_category: businessCategory,
    candidate_lat: coords.lat,
    candidate_lon: coords.lon,
    floor_area: floorArea
  };
}

function renderResult(result) {
  const summary = document.getElementById("resultSummary");
  const tableWrap = document.getElementById("competitorTable");

  const predictedVisits = result.predicted_visits ?? "N/A";
  const marketShare = Number(result.market_share);
  const runtime = result.runtime_ms ?? "N/A";
  const notes = result.notes ?? "";

  summary.innerHTML = `
    <strong>Predicted Visits:</strong> ${escapeHtml(predictedVisits)}<br>
    <strong>Estimated Market Share:</strong> ${Number.isFinite(marketShare) ? (marketShare * 100).toFixed(2) + "%" : "N/A"}<br>
    <strong>Runtime:</strong> ${escapeHtml(runtime)} ms<br>
    <strong>Notes:</strong> ${escapeHtml(notes)}
  `;

  const competitors = Array.isArray(result.competitors) ? result.competitors : [];

  if (competitors.length === 0) {
    tableWrap.innerHTML = "No competitor records returned.";
    return;
  }

  tableWrap.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Distance</th>
          <th>Size</th>
          <th>Attraction</th>
        </tr>
      </thead>
      <tbody>
        ${competitors.map(c => `
          <tr>
            <td>${escapeHtml(c.name ?? c.place_name ?? c.poi_name ?? "Unknown")}</td>
            <td>${escapeHtml(c.distance_miles ?? c.distance ?? "N/A")}</td>
            <td>${escapeHtml(c.size ?? c.floor_area ?? c.area ?? "N/A")}</td>
            <td>${escapeHtml(c.attraction ?? "N/A")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function parseCoordinates(text) {
  /*
    Supports:
    42.229212, -71.805525
    use 42.229212, -71.805525 and rerun...
  */
  const match = text.match(/(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)/);

  if (!match) {
    return null;
  }

  const lat = Number(match[1]);
  const lon = Number(match[2]);

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return null;
  }

  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return null;
  }

  return { lat, lon };
}

function addBotMessage(text) {
  addMessage(text, "bot");
}

function addUserMessage(text) {
  addMessage(text, "user");
}

function addErrorMessage(text) {
  addMessage(text, "error");
}

function addMessage(text, type) {
  const div = document.createElement("div");
  div.className = `message ${type}`;
  div.innerText = text;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
