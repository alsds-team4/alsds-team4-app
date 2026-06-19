const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
const saveScenarioBtn = document.getElementById("saveScenarioBtn");
const clearScenariosBtn = document.getElementById("clearScenariosBtn");
const resetScenarioBtn = document.getElementById("resetScenarioBtn");

const openCategoryPopupBtn = document.getElementById("openCategoryPopupBtn");
const closeCategoryPopupBtn = document.getElementById("closeCategoryPopupBtn");
const categoryModal = document.getElementById("categoryModal");
const categorySearchInput = document.getElementById("categorySearchInput");
const categoryTableBody = document.getElementById("categoryTableBody");
const categoryLoadingText = document.getElementById("categoryLoadingText");

let categoryCache = [];
let categoryLoaded = false;


const state = {
  step: "category",
  business_category: null,
  display_category: null,
  candidate_lat: null,
  candidate_lon: null,
  floor_area: null,
  last_result: null,
  current_result_saved: false,
  saved_scenarios: []
};

const LIMITS = {
  maxCategoryLength: 80,
  minFloorArea: 10,
  maxFloorArea: 500000
};

loadSavedScenarios();
updateWorkflowStep(1);
renderSavedScenarios();

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

if (resetScenarioBtn) {
  resetScenarioBtn.addEventListener("click", restartChat);
}

if (saveScenarioBtn) {
  saveScenarioBtn.addEventListener("click", saveCurrentScenario);
}

if (clearScenariosBtn) {
  clearScenariosBtn.addEventListener("click", clearSavedScenarios);
}

if (openCategoryPopupBtn) {
  openCategoryPopupBtn.addEventListener("click", openCategoryPopup);
}

if (closeCategoryPopupBtn) {
  closeCategoryPopupBtn.addEventListener("click", closeCategoryPopup);
}

if (categoryModal) {
  categoryModal.addEventListener("click", function (event) {
    if (event.target === categoryModal) {
      closeCategoryPopup();
    }
  });
}

if (categorySearchInput) {
  categorySearchInput.addEventListener("input", function () {
    renderCategoryTable(categorySearchInput.value);
  });
}

if (categoryTableBody) {
  categoryTableBody.addEventListener("click", function (event) {
    const button = event.target.closest(".use-category-btn");

    if (!button) {
      return;
    }

    const naicsCode = button.dataset.naicsCode;
    const storeType = button.dataset.storeType;

    useCategoryFromPopup(naicsCode, storeType);
  });
}



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
    updateWorkflowStep(2);
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
      restartChat();
      return;
    }

    if (lowerText === "clear saved scenarios" || lowerText === "clear scenarios") {
      clearSavedScenarios();
      return;
    }

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
    const message = error.message || String(error);

    if (message.toLowerCase().includes("no historical records")) {
      addBotMessage(
        "There are no historical records for this NAICS code / business category in our data, " +
        "and therefore the model cannot produce results for this NAICS code. " +
        "Please try another NAICS code / business category."
      );
    } else {
      addErrorMessage(message);
    }

    state.step = "category";
    updateWorkflowStep(1);
    showSaveButton(false);
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
  state.current_result_saved = false;

  updateWorkflowStep(2);
  showSaveButton(false);

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
      updateWorkflowStep(2);

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
  updateWorkflowStep(2);

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
  updateWorkflowStep(2);

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
  state.current_result_saved = false;

  updateWorkflowStep(3);
  showSaveButton(false);

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
  state.current_result_saved = false;

  updateWorkflowStep(3);
  showSaveButton(false);

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
  state.current_result_saved = false;

  renderResult(data.result);
  updateWorkflowStep(3);
  showSaveButton(true);

  if (window.plotCompetitors) {
    window.plotCompetitors(data.result.competitors);
  }

  addBotMessage(
    data.explanation ||
    "Model completed. You can now ask follow-up questions about the result, save this location, or type 'new scenario' to evaluate another location."
  );

  addBotMessage(
    "You can ask a follow-up question, click 'Save This Location', or type 'new scenario' to start over."
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

function saveCurrentScenario() {
  if (!state.last_result) {
    addBotMessage("Please run the model before saving a scenario.");
    return;
  }

  if (state.current_result_saved) {
    addBotMessage("This result has already been saved. Type 'new scenario' to evaluate another location.");
    return;
  }

  const marketShare = Number(state.last_result.market_share);
  const predictedVisits = Number(state.last_result.predicted_visits);
  const competitors = Array.isArray(state.last_result.competitors)
    ? state.last_result.competitors
    : [];

  const scenario = {
    id: Date.now(),
    label: `Site ${state.saved_scenarios.length + 1}`,
    business_category: state.display_category || state.business_category || "Unknown",
    candidate_lat: state.candidate_lat,
    candidate_lon: state.candidate_lon,
    floor_area: state.floor_area,
    predicted_visits: Number.isFinite(predictedVisits) ? predictedVisits : null,
    market_share: Number.isFinite(marketShare) ? marketShare : null,
    competitor_count: competitors.length
  };

  state.saved_scenarios.push(scenario);
  state.current_result_saved = true;

  saveScenariosToStorage();
  renderSavedScenarios();
  updateWorkflowStep(4);
  showSaveButton(false);

  addBotMessage(
    `${scenario.label} saved. Type 'new scenario' to test another location, then save it to compare scenarios side by side.`
  );
}

function renderSavedScenarios() {
  const scenarioList = document.getElementById("scenarioList");
  const scenarioCount = document.getElementById("scenarioCount");
  const comparisonPanel = document.getElementById("comparisonPanel");
  const comparisonTableBody = document.getElementById("comparisonTableBody");

  if (!scenarioList || !scenarioCount || !comparisonPanel || !comparisonTableBody) {
    return;
  }

  scenarioCount.textContent = `${state.saved_scenarios.length} saved`;

  if (clearScenariosBtn) {
    clearScenariosBtn.style.display = state.saved_scenarios.length > 0 ? "block" : "none";
  }

  if (state.saved_scenarios.length === 0) {
    scenarioList.innerHTML = `
      <div class="scenario-empty">
        No scenarios saved yet. Run the model and save a location to compare results.
      </div>
    `;
    comparisonPanel.style.display = "none";
    comparisonTableBody.innerHTML = "";
    return;
  }

  const bestScenarioId = getBestScenarioId();

  scenarioList.innerHTML = state.saved_scenarios.map((scenario) => {
    const isBest = scenario.id === bestScenarioId && state.saved_scenarios.length >= 2;
    const marketSharePercent = formatMarketShare(scenario.market_share);
    const visits = formatNumber(scenario.predicted_visits);

    return `
      <div class="scenario-card ${isBest ? "best" : ""}">
        <div class="scenario-title">
          <span>${escapeHtml(scenario.label)}</span>
          ${isBest ? `<span class="best-label">Best</span>` : ""}
        </div>
        <div class="scenario-meta">
          <strong>Store:</strong> ${escapeHtml(scenario.business_category)}<br>
          <strong>Location:</strong> ${formatCoordinate(scenario.candidate_lat)}, ${formatCoordinate(scenario.candidate_lon)}<br>
          <strong>Floor Area:</strong> ${escapeHtml(scenario.floor_area)} sqm
        </div>
        <div class="scenario-metrics">
          <div class="scenario-metric">
            <div class="metric-value">${visits}</div>
            <div class="metric-label">Predicted Visits</div>
          </div>
          <div class="scenario-metric">
            <div class="metric-value">${marketSharePercent}</div>
            <div class="metric-label">Market Share</div>
          </div>
        </div>
        <button class="scenario-remove" onclick="removeScenario(${scenario.id})">Remove</button>
      </div>
    `;
  }).join("");

  if (state.saved_scenarios.length >= 2) {
    comparisonPanel.style.display = "block";

    comparisonTableBody.innerHTML = state.saved_scenarios.map((scenario) => {
      const isBest = scenario.id === bestScenarioId;
      return `
        <tr>
          <td>${isBest ? "Best - " : ""}${escapeHtml(scenario.label)}</td>
          <td>${escapeHtml(scenario.business_category)}</td>
          <td>${formatNumber(scenario.predicted_visits)}</td>
          <td>${formatMarketShare(scenario.market_share)}</td>
          <td>${escapeHtml(scenario.competitor_count)}</td>
        </tr>
      `;
    }).join("");
  } else {
    comparisonPanel.style.display = "none";
    comparisonTableBody.innerHTML = "";
  }
}

function getBestScenarioId() {
  if (state.saved_scenarios.length === 0) {
    return null;
  }

  let best = state.saved_scenarios[0];

  state.saved_scenarios.forEach((scenario) => {
    const currentVisits = Number(scenario.predicted_visits ?? -1);
    const bestVisits = Number(best.predicted_visits ?? -1);

    if (currentVisits > bestVisits) {
      best = scenario;
      return;
    }

    if (currentVisits === bestVisits) {
      const currentShare = Number(scenario.market_share ?? -1);
      const bestShare = Number(best.market_share ?? -1);

      if (currentShare > bestShare) {
        best = scenario;
      }
    }
  });

  return best.id;
}

function removeScenario(id) {
  state.saved_scenarios = state.saved_scenarios.filter((scenario) => scenario.id !== id);
  saveScenariosToStorage();
  renderSavedScenarios();

  if (state.saved_scenarios.length > 0) {
    updateWorkflowStep(4);
  }
}

window.removeScenario = removeScenario;

function clearSavedScenarios() {
  state.saved_scenarios = [];
  saveScenariosToStorage();
  renderSavedScenarios();
  addBotMessage("Saved scenarios have been cleared.");
}

function saveScenariosToStorage() {
  try {
    localStorage.setItem("alsds_saved_scenarios", JSON.stringify(state.saved_scenarios));
  } catch (error) {
    console.warn("Could not save scenarios to localStorage:", error);
  }
}

function loadSavedScenarios() {
  try {
    const stored = localStorage.getItem("alsds_saved_scenarios");
    if (!stored) {
      state.saved_scenarios = [];
      return;
    }

    const parsed = JSON.parse(stored);
    state.saved_scenarios = Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    state.saved_scenarios = [];
  }
}

function showSaveButton(visible) {
  if (!saveScenarioBtn) {
    return;
  }

  saveScenarioBtn.style.display = visible ? "block" : "none";
}

function updateWorkflowStep(stepNumber) {
  for (let i = 1; i <= 4; i++) {
    const stepElement = document.getElementById(`step-${i}`);
    if (!stepElement) continue;

    stepElement.classList.toggle("active", i === stepNumber);
    stepElement.classList.toggle("done", i < stepNumber);
  }

  const badge = document.getElementById("chatStepBadge");
  if (badge) {
    badge.textContent = `Step ${stepNumber}`;
  }
}

function parseCoordinates(text) {
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
  state.current_result_saved = false;

  const summary = document.getElementById("resultSummary");
  const tableWrap = document.getElementById("competitorTable");

  if (summary) {
    summary.innerHTML = "No model result yet.";
  }

  if (tableWrap) {
    tableWrap.innerHTML = "";
  }

  showSaveButton(false);
  updateWorkflowStep(1);
}

function restartChat() {
  resetScenario();

  if (chatMessages) {
    chatMessages.innerHTML = "";
  }

  if (chatInput) {
    chatInput.value = "";
  }

  addBotMessage(
    "Welcome. I will guide you through a new store-location scenario for Worcester, MA. " +
    "First, enter a store type or NAICS code. For example: liquor store or 445310."
  );
}


async function openCategoryPopup() {
  if (!categoryModal) {
    return;
  }

  categoryModal.style.display = "flex";

  if (categorySearchInput) {
    categorySearchInput.value = "";
  }

  if (!categoryLoaded) {
    await loadCategoriesFromAzure();
  } else {
    renderCategoryTable("");
  }

  if (categorySearchInput) {
    categorySearchInput.focus();
  }
}

function closeCategoryPopup() {
  if (!categoryModal) {
    return;
  }

  categoryModal.style.display = "none";
}

async function loadCategoriesFromAzure() {
  if (categoryLoadingText) {
    categoryLoadingText.textContent = "Loading categories from Azure SQL...";
  }

  if (categoryTableBody) {
    categoryTableBody.innerHTML = "";
  }

  try {
    const response = await fetch("/api/categories");
    const data = await response.json();

    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Could not load categories.");
    }

    categoryCache = Array.isArray(data.categories) ? data.categories : [];
    categoryLoaded = true;

    if (categoryLoadingText) {
      categoryLoadingText.textContent = `${categoryCache.length} supported categories loaded from Azure SQL.`;
    }

    renderCategoryTable("");

  } catch (error) {
    if (categoryLoadingText) {
      categoryLoadingText.textContent = `Failed to load categories: ${error.message}`;
    }

    if (categoryTableBody) {
      categoryTableBody.innerHTML = `
        <tr>
          <td colspan="3" class="category-empty-row">
            Could not load categories from Azure SQL.
          </td>
        </tr>
      `;
    }
  }
}

function renderCategoryTable(filterText) {
  if (!categoryTableBody) {
    return;
  }

  const filter = String(filterText || "").trim().toLowerCase();

  const filtered = categoryCache.filter((item) => {
    const storeType = String(item.store_type || "").toLowerCase();
    const naicsCode = String(item.naics_code || "").toLowerCase();

    return (
      !filter ||
      storeType.includes(filter) ||
      naicsCode.includes(filter)
    );
  });

  if (filtered.length === 0) {
    categoryTableBody.innerHTML = `
      <tr>
        <td colspan="3" class="category-empty-row">
          No matching store type or NAICS code found.
        </td>
      </tr>
    `;
    return;
  }

  categoryTableBody.innerHTML = filtered.map((item) => {
    const storeType = escapeHtml(item.store_type || "Unknown");
    const naicsCode = escapeHtml(item.naics_code || "");

    return `
      <tr>
        <td>${storeType}</td>
        <td><span class="category-code">${naicsCode}</span></td>
        <td>
          <button
            type="button"
            class="use-category-btn"
            data-naics-code="${naicsCode}"
            data-store-type="${storeType}"
          >
            Use
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

function useCategoryFromPopup(naicsCode, storeType) {
  closeCategoryPopup();

  if (!naicsCode) {
    addBotMessage("This category does not have a valid NAICS code.");
    return;
  }

  if (state.step !== "category") {
    addBotMessage(
      `Selected category: ${storeType} (${naicsCode}). Type "new scenario" first if you want to use it for a new model run.`
    );
    return;
  }

  state.business_category = naicsCode;
  state.display_category = `${storeType} (${naicsCode})`;
  state.step = "latitude";
  state.current_result_saved = false;

  updateWorkflowStep(2);
  showSaveButton(false);

  addUserMessage(`${storeType} (${naicsCode})`);

  addBotMessage(
    `Good. I will use "${storeType}" with NAICS code ${naicsCode}. ` +
    "Now enter the proposed store latitude as a number. For example: 42.27. " +
    "You can also click the map to select a location."
  );
}


function formatMarketShare(value) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return "N/A";
  }

  return `${(number * 100).toFixed(2)}%`;
}

function formatNumber(value) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return "N/A";
  }

  return number.toFixed(2);
}

function formatCoordinate(value) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return "N/A";
  }

  return number.toFixed(5);
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
