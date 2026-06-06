const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");

const state = {
  step: "category",
  business_category: null,
  display_category: null,
  candidate_lat: null,
  candidate_lon: null,
  floor_area: null,
  last_result: null
};

const LIMITS = {
  maxCategoryLength: 80,
  minFloorArea: 10,
  maxFloorArea: 500000
};

addBotMessage(
  "Welcome. I will guide you through a store-location scenario for Worcester, MA. " +
  "First, enter a store type or NAICS code. For example: liquor store or 445310."
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

  if (state.step === "latitude" || state.step === "longitude") {
    addBotMessage(
      `Great, I captured the candidate location from the map: ` +
      `${location.lat.toFixed(6)}, ${location.lon.toFixed(6)}. ` +
      "Now enter the proposed store floor area in square meters. For example: 2500."
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
    const lowerText = text.toLowerCase();

    if (lowerText === "new scenario" || lowerText === "restart" || lowerText === "start over") {
      resetScenario();
      addBotMessage(
        "Sure. Let's start a new scenario. Enter a store type or NAICS code. For example: liquor store or 445310."
      );
      return;
    }

    /*
      Supported rerun example:
      "use 42.229212, -71.805525 and rerun the model for liquor store and area of 1000 square meters"
      "use 42.229212, -71.805525 and rerun the model for NAICS code 445310 and area of 1000 square meters"
    */
    const rerunInputs = extractRerunInputs(text);

    if (rerunInputs) {
      await rerunModelFromMessage(rerunInputs);
      return;
    }

    if (state.step === "category") {
      handleCategoryStep(text);
      return;
    }

    if (state.step === "latitude") {
      handleLatitudeStep(text);
      return;
    }

    if (state.step === "longitude") {
      handleLongitudeStep(text);
      return;
    }

    if (state.step === "floor_area") {
      await handleFloorAreaStep(text);
      return;
    }

    if (state.step === "running") {
      addBotMessage("The model is currently running. Please wait for the result.");
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

function handleCategoryStep(text) {
  const parsed = parseBusinessCategory(text);

  if (!parsed.ok) {
    addBotMessage(parsed.error);
    return;
  }

  state.business_category = parsed.value;
  state.display_category = parsed.display;
  state.step = "latitude";

  addBotMessage(
    `Good. I will use "${state.display_category}" as the store category. ` +
    "Now enter the proposed store latitude as a number. For example: 42.27. " +
    "You can also click the map to select a location."
  );
}

function handleLatitudeStep(text) {
  const mapCommand = text.trim().toLowerCase();

  if (mapCommand === "use map") {
    if (state.candidate_lat !== null && state.candidate_lon !== null) {
      state.step = "floor_area";
      addBotMessage(
        `Great. I used the selected map location: ` +
        `${state.candidate_lat.toFixed(6)}, ${state.candidate_lon.toFixed(6)}. ` +
        "Now enter the proposed store floor area in square meters. For example: 2500."
      );
      return;
    }

    addBotMessage(
      "I do not see a selected map location yet. Please click the map first, or enter a latitude such as 42.27."
    );
    return;
  }

  const parsed = parseLatitude(text);

  if (!parsed.ok) {
    addBotMessage(parsed.error);
    return;
  }

  state.candidate_lat = parsed.value;
  state.step = "longitude";

  addBotMessage(
    "Great. Now enter the proposed store longitude as a number. For Worcester, an example is -71.80."
  );
}

function handleLongitudeStep(text) {
  const parsed = parseLongitude(text);

  if (!parsed.ok) {
    addBotMessage(parsed.error);
    return;
  }

  state.candidate_lon = parsed.value;

  if (window.setCandidateLocation) {
    window.setCandidateLocation(state.candidate_lat, state.candidate_lon, false);
  }

  state.step = "floor_area";

  addBotMessage(
    `Great. The proposed location is latitude ${state.candidate_lat.toFixed(6)}, ` +
    `longitude ${state.candidate_lon.toFixed(6)}. ` +
    "Now enter the proposed store floor area in square meters. For example: 2500."
  );
}

async function handleFloorAreaStep(text) {
  const parsed = parseFloorArea(text);

  if (!parsed.ok) {
    addBotMessage(parsed.error);
    return;
  }

  state.floor_area = parsed.value;
  state.step = "running";

  addBotMessage(
    `Thanks. I will run the Huff model for ${state.display_category}, ` +
    `location (${state.candidate_lat.toFixed(6)}, ${state.candidate_lon.toFixed(6)}), ` +
    `and floor area ${state.floor_area} square meters.`
  );

  await runModel();
}

function parseBusinessCategory(text) {
  const value = String(text || "").trim().replace(/\s+/g, " ");

  if (!value) {
    return {
      ok: false,
      error: "Please enter a store type or NAICS code. For example: liquor store or 445310."
    };
  }

  if (value.length > LIMITS.maxCategoryLength) {
    return {
      ok: false,
      error: `Store type is too long. Please keep it under ${LIMITS.maxCategoryLength} characters. For example: liquor store.`
    };
  }

  if (/^\d+$/.test(value)) {
    if (value.length < 2 || value.length > 6) {
      return {
        ok: false,
        error: "Invalid NAICS code. Please enter a 2–6 digit NAICS code. For example: 445310."
      };
    }

    return {
      ok: true,
      value: value,
      display: value
    };
  }

  if (!/^[a-zA-Z0-9\s,&'()/-]+$/.test(value)) {
    return {
      ok: false,
      error: "Invalid store type. Please use a simple store name, such as liquor store, grocery store, restaurant, or pharmacy."
    };
  }

  return {
    ok: true,
    value: value,
    display: value
  };
}

function parseLatitude(text) {
  const value = Number(String(text).trim());

  if (!Number.isFinite(value)) {
    return {
      ok: false,
      error: "Invalid latitude. Please enter a number only. For example: 42.27."
    };
  }

  if (value < -90 || value > 90) {
    return {
      ok: false,
      error: "Latitude must be between -90 and 90. For Worcester, an example is 42.27."
    };
  }

  return {
    ok: true,
    value: value
  };
}

function parseLongitude(text) {
  const value = Number(String(text).trim());

  if (!Number.isFinite(value)) {
    return {
      ok: false,
      error: "Invalid longitude. Please enter a number only. For example: -71.80."
    };
  }

  if (value < -180 || value > 180) {
    return {
      ok: false,
      error: "Longitude must be between -180 and 180. For Worcester, an example is -71.80."
    };
  }

  return {
    ok: true,
    value: value
  };
}

function parseFloorArea(text) {
  const value = Number(String(text).replace(/,/g, "").trim());

  if (!Number.isFinite(value)) {
    return {
      ok: false,
      error: "Invalid floor area. Please enter a positive number in square meters. For example: 2500."
    };
  }

  if (value < LIMITS.minFloorArea || value > LIMITS.maxFloorArea) {
    return {
      ok: false,
      error: `Floor area should be between ${LIMITS.minFloorArea} and ${LIMITS.maxFloorArea} square meters. For example: 2500.`
    };
  }

  return {
    ok: true,
    value: value
  };
}

async function rerunModelFromMessage(inputs) {
  state.business_category = inputs.business_category;
  state.display_category = inputs.business_category;
  state.candidate_lat = inputs.candidate_lat;
  state.candidate_lon = inputs.candidate_lon;
  state.floor_area = inputs.floor_area;
  state.step = "running";

  addBotMessage(
    `I found a new complete model input set. I will rerun the Huff model for ${state.business_category}, ` +
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

      // Optional aliases for backend compatibility
      naics_code: state.business_category,
      floor_area_sqm: state.floor_area
    })
  });

  const text = await response.text();

  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new Error(
      "The server did not return valid JSON. Please check whether /api/run_huff is deployed correctly."
    );
  }

  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Model failed.");
  }

  state.last_result = data.result;
  state.step = "ready";

  renderResult(data.result);

  if (window.plotCompetitors) {
    window.plotCompetitors(data.result.competitors);
  }

  addBotMessage(
    data.explanation ||
    "Model completed. You can now ask follow-up questions about the result, or type 'new scenario' to run another location."
  );

  addBotMessage(
    "You can ask a follow-up question, or type 'new scenario' to start over."
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

  const categoryMatch =
    message.match(/naics(?:\s+code)?\s*(?:is|=|:|of|for)?\s*(\d{2,6})/i) ||
    message.match(/business\s+category\s*(?:is|=|:|of|for)?\s*([a-zA-Z0-9\s,&'()/-]{2,80})/i) ||
    message.match(/store\s*type\s*(?:is|=|:|of|for)?\s*([a-zA-Z0-9\s,&'()/-]{2,80})/i) ||
    message.match(/for\s+([a-zA-Z][a-zA-Z0-9\s,&'()/-]{2,80})\s+and\s+area/i);

  const areaMatch =
    message.match(/area\s*(?:of|is|=|:)?\s*([\d,]+(?:\.\d+)?)/i) ||
    message.match(/floor\s+area\s*(?:of|is|=|:)?\s*([\d,]+(?:\.\d+)?)/i) ||
    message.match(/([\d,]+(?:\.\d+)?)\s*(?:square\s+meters|square\s+metres|sqm|sq\.?\s*m|m2|m²)/i);

  if (!categoryMatch || !areaMatch) {
    return null;
  }

  const businessCategory = String(categoryMatch[1]).trim();
  const floorArea = Number(areaMatch[1].replace(/,/g, ""));

  const parsedCategory = parseBusinessCategory(businessCategory);
  const parsedArea = parseFloorArea(String(floorArea));

  if (!parsedCategory.ok || !parsedArea.ok) {
    return null;
  }

  return {
    business_category: parsedCategory.value,
    candidate_lat: coords.lat,
    candidate_lon: coords.lon,
    floor_area: parsedArea.value
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
    <strong>Data Source:</strong> Azure SQL<br>
    <strong>How to read this:</strong> Predicted visits estimate the number of customer trips captured by the proposed store. Market share estimates the store's share of category demand compared with nearby competitors.<br>
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
    <p class="hint">Higher attraction means a stronger nearby competitor based on store size and distance.</p>
  `;
}

function parseCoordinates(text) {
  /*
    Supports:
    42.229212, -71.805525
    use 42.229212, -71.805525 and rerun...
  */
  const match = String(text).match(/(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)/);

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

  return {
    lat: lat,
    lon: lon
  };
}

function resetScenario() {
  state.step = "category";
  state.business_category = null;
  state.display_category = null;
  state.candidate_lat = null;
  state.candidate_lon = null;
  state.floor_area = null;
  state.last_result = null;

  const summary = document.getElementById("resultSummary");
  const tableWrap = document.getElementById("competitorTable");

  if (summary) {
    summary.innerHTML = "No model result yet.";
  }

  if (tableWrap) {
    tableWrap.innerHTML = "";
  }
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





