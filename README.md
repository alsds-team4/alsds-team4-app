# AI-Assisted Location Decision Support System (ALSDS)

## Team Seven Hills Analytics

**Team Members**

* Wilson ChungNam Wan
* Omotolani Orekoya
* Sharelle Allen
* Rojisha Awale

## Project Overview

This project is an AI-assisted location decision support system for evaluating potential business locations in Worcester, Massachusetts. The system uses a Huff Model to estimate how much customer demand a proposed store location may capture based on store category, store size, nearby competitors, and geographic distance.

The purpose of the project is to help non-technical users, such as entrepreneurs or local business planners, make more informed location decisions. Instead of requiring users to manually analyze geographic and competitor data, the dashboard guides them through a step-by-step workflow and returns model-based estimates such as predicted visits, estimated market share, nearby competitor information, and a plain-English explanation of the result.

## What the System Does

The dashboard allows users to:

1. Enter a business type or NAICS code.
2. Select a proposed store location by typing latitude and longitude or clicking the map.
3. Enter the proposed store floor area.
4. Run the Huff Model using Azure SQL data.
5. Review predicted visits, estimated market share, runtime, and nearby competitor attraction scores.
6. Ask follow-up questions using the guided chatbot.
7. Save multiple scenarios and compare location results side by side.

The final system is designed to be more than a technical prototype. It is intended to function as a user-friendly decision support system that helps users understand both the model output and the business meaning behind the results.

## Deployed Application

**Live Web App:**
https://alsds-team4-app-cccva7abd9a3aue6.eastus-01.azurewebsites.net/

**Database Structure Check:**
https://alsds-team4-app-cccva7abd9a3aue6.eastus-01.azurewebsites.net/db_structure

## Technology Stack

* **Frontend:** HTML, CSS, JavaScript
* **Backend:** Python Flask
* **Mapping:** Leaflet.js and OpenStreetMap
* **Model:** Huff Model location analysis
* **Database:** Azure SQL
* **Deployment:** Azure App Service through GitHub Actions
* **AI Support:** GPT-based explanation endpoint for model interpretation

## Key Features Built by Our Team

### 1. Azure SQL Migration

The original project relied on a local SQLite database. Our team migrated the application workflow to Azure SQL so that the deployed web app can query cloud-hosted data instead of relying on a local database file.

The migration process included:

* Creating `migrate_to_azure_sql.py`
* Copying core data tables from SQLite to Azure SQL
* Verifying migrated tables and row counts through `/db_structure`
* Updating the Huff Model engine to use Azure SQL through `db.py`
* Keeping database credentials secure through Azure App Service environment variables

The app does not hard-code database usernames or passwords in GitHub. Instead, the deployed Azure Web App reads the secure `SQL_CONNECTION_STRING` environment variable and uses it through `db.py`.

### 2. Huff Model Integration with Azure SQL

We updated `huff_engine.py` so the model queries Azure SQL rather than opening the original local SQLite database. The model keeps the required function structure while using cloud data for competitors, demand, calibrated parameters, and distance-related calculations.

The model output includes:

* Predicted visits
* Estimated market share
* Nearby competitors
* Attraction scores
* Runtime
* Model notes

### 3. Improved Guided Chatbot Workflow

We improved the chatbot so that users are guided through the location analysis one step at a time. The chatbot now supports:

* Store type or NAICS code input
* Separate latitude and longitude prompts
* Clear examples for valid inputs
* Input validation for invalid numbers or unrealistic ranges
* Error prompts when the user enters incorrect values
* A visible **Reset / New Scenario** button to restart the workflow without refreshing the page
* Follow-up questions after the model result is generated

This helps reduce confusion for non-technical users and prevents avoidable input errors before the model is run.

### 4. Dashboard UI Improvements

We improved the user interface to make the dashboard easier to understand and use. Major UI improvements include:

* A workflow step bar showing the user’s progress
* Better input guidance in the chatbot panel
* A map legend to explain symbols on the map
* Reduced blank space between the map and model result panel
* A clearer model result panel with plain-English interpretation
* A saved scenarios panel for comparing multiple location choices
* A side-by-side comparison table for saved results

These changes support the design principles of reducing cognitive load, supporting recognition instead of recall, improving trust through explanation, and helping users compare scenarios visually.

### 5. Saved Scenario Comparison

The dashboard includes a lightweight scenario comparison feature. After running the model, users can save the result and compare it with other saved locations. The comparison panel displays key decision metrics such as:

* Scenario name
* Store type or NAICS code
* Predicted visits
* Estimated market share
* Number of nearby competitors

This helps users evaluate multiple possible store locations without manually recording model results.

## NAICS Code Handling

The system is designed to handle NAICS codes carefully because not all NAICS codes have calibrated model parameters.

The intended NAICS logic is:

1. **NAICS code exists in the calibrated parameters table**
   The model uses the calibrated alpha and beta values.

2. **NAICS code exists in the POI data but not in the calibrated parameters table**
   The model can use fallback default values:

   * alpha = 1
   * beta = 2

3. **NAICS code does not exist in the POI data**
   The system should not generate a model result. Instead, the chatbot should clearly explain that there are no historical records for that NAICS code or business category in the dataset.

This prevents the system from inventing results for unsupported business categories.

## How This Version Is Different from the Baseline

The baseline project was primarily a technical prototype using local data and a simpler dashboard workflow. Our version extends the baseline in several important ways:

* Migrated the database workflow from local SQLite to Azure SQL
* Updated the Huff Model engine to query cloud-hosted data
* Added Azure deployment through GitHub Actions
* Added database structure verification
* Improved the chatbot with validation and guided steps
* Improved the dashboard layout and visual clarity
* Added scenario saving and comparison support
* Added clearer model interpretation for non-technical users
* Improved the system’s handling of NAICS code inputs

Overall, our team’s version is more cloud-based, more user-friendly, and more focused on helping users make practical location decisions.

## Important Notes

Migration endpoints were used during development to move data from SQLite to Azure SQL. These endpoints are temporary development tools and should be disabled or removed in the final deployed version of the application.

The final application should avoid exposing administrative migration routes to regular users.

## Repository Structure

```text
.
├── app.py
├── db.py
├── huff_engine.py
├── migrate_to_azure_sql.py
├── requirements.txt
├── startup.sh
├── Data/
├── static/
│   ├── chat.js
│   ├── map.js
│   └── styles.css
├── templates/
│   └── index.html
└── README.md
```

## How to Use the Dashboard

1. Open the deployed web application.
2. Enter a store type or NAICS code.
3. Enter latitude and longitude, or click the map to select a location.
4. Enter the proposed store floor area.
5. Review the Huff Model result.
6. Save the scenario if you want to compare it with another location.
7. Use **Reset / New Scenario** to evaluate another site.
8. Compare saved scenarios in the right-side comparison panel.

## Project Goal

The goal of this project is to make location analysis easier for users who may not have technical, geographic, or data science expertise. By combining a guided dashboard, a Huff Model, Azure SQL data, and natural-language explanations, the system helps users understand how a proposed store location may perform relative to nearby competitors and customer demand.
