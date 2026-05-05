# Data Dictionary: Worcester Urban Analytics Engine

This repository contains six core datasets used to drive the Spatial Decision Support System (SDSS) for Worcester, MA. These files allow the system to calculate market share probabilities using the Huff Gravity Model and provide demographic context for retail site selection.

---

## 1. Demographic Profiles (`worcester_cbgs.csv`)
This file represents the **Demand Side** of the model. It contains socioeconomic data for every Census Block Group (CBG) in Worcester.

| Column | Description | Data Type |
| :--- | :--- | :--- |
| **cbg** | Unique 12-digit FIPS code for the Census Block Group. | String (ID) |
| **total_population** | Total number of residents in the CBG. | Integer |
| **median_household_income** | Median annual household income (USD). | Float |
| **median_age** | Median age of the population. | Float |
| **white_population** | Proportion of population identifying as White. | Float (0-1) |
| **black_population** | Proportion of population identifying as Black. | Float (0-1) |
| **asian_population** | Proportion of population identifying as Asian. | Float (0-1) |
| **hispanic_population** | Proportion of population identifying as Hispanic. | Float (0-1) |
| **uni_degree** | Proportion of population with a University degree or higher. | Float (0-1) |
| **income_q** | Income Quartile (Q1-Q4) relative to Worcester averages. | String |
| **education_q** | Education level Quartile (Q1-Q4). | String |

---

## 2. Business Locations (`worcester_pois.csv`)
This file represents the **Competitive Landscape**. It contains attributes for existing Points of Interest (POIs) in the study area.

| Column | Description | Data Type |
| :--- | :--- | :--- |
| **placekey** | Unique global identifier for the business location. | String (ID) |
| **location_name** | The trade name of the business. | String |
| **top_category** | High-level business classification (e.g., Restaurants, Retail). | String |
| **latitude** | WGS84 Decimal Latitude of the storefront. | Float |
| **longitude** | WGS84 Decimal Longitude of the storefront. | Float |
| **poi_cbg** | The ID of the CBG where the business is physically located. | String (ID) |
| **wkt_area_sq_meters** | The physical footprint of the building in square meters. | Float |

---

## 3. Distance Matrix (`worcester_cbg_poi_distance.csv`)
The "Speed Layer" containing pre-computed pairwise distances between residents and businesses to optimize mathematical simulations.

| Column | Description | Data Type |
| :--- | :--- | :--- |
| **placekey** | Foreign key referencing the business location. | String (ID) |
| **GEOID10** | Foreign key referencing the resident CBG. | String (ID) |
| **distance_m** | Direct distance between CBG centroid and POI in **meters**. | Float |

---

## 4. Mobility Ground Truth (`worcester_cbg_poi_visits.csv`)
This file contains aggregated visit counts used to calibrate the model's lambda ($\lambda$) distance-decay parameters.

| Column | Description | Data Type |
| :--- | :--- | :--- |
| **visitor_home_cbg** | Origin neighborhood of the visitors. | String (ID) |
| **placekey** | Destination business location. | String (ID) |
| **visit_count** | Total visits recorded from that origin to that destination. | Integer |

---

## 5. Spatial Boundaries (`worcester_cbgs_map.geojson`)
A geographic vector file used for frontend rendering and spatial visualization.

* **Format:** GeoJSON (FeatureCollection).
* **Properties:** Contains `GEOID10` to join with the CSV datasets.
* **Coordinate System:** WGS84 (EPSG:4326).

---

## 6. Model Parameters (`calibrated_parameters_filtered.csv`)
This file contains the industry-specific coefficients for the Huff Gravity Model. This version has been strictly filtered to include only high-performing categories (Correlation > 0.25) with sufficient historical data (> 50 records). 
For this project, a grid search approach was utilized to compute the parameters alpha ($$\alpha$$) and beta ($$\beta$$) (See the code notebook `worcester_huff_gravity_parameter_calibration.ipynb`).

| Column | Description | Data Type |
| :--- | :--- | :--- |
| **top_category** | High-level business classification. | String |
| **NAICS code** | The standard North American Industry Classification System code. | Integer |
| **alpha ($$\alpha$$)** | **Attractiveness Sensitivity**. Represents the "Pull" of the store's physical size. | Float |
| **beta ($$\beta$$)** | **Distance Decay**. Represents the "Friction" of travel distance to the site. | Float |
| **correlation** | The Pearson Correlation (r) between predicted shares and historical visit data. | Float (0-1) |

---

## **Implementation Logic for Developers**
To calculate the probability ($P_{ij}$) that a resident from a neighborhood ($i$) will visit a specific site ($j$), use the following formula integrated with these parameters:

$$U_{ij} = \frac{Area_j^\alpha}{Distance_{ij}^\beta}$$

$$P_{ij} = \frac{U_{ij}}{\sum_{k=1}^n U_{ik}}$$


### **Development Guidelines:**
* **Parameter Selection and Category Match:** Query the `ModelParameters` table using the `top_category` or `NAICS code` associated with the user's business. Your backend should query this file (or an equivalent SQL table) based on the `top_category` or `NAICS code`of the user's business.
* **Predictive Simulation:** Apply the corresponding Alpha and Beta to calculate the neighborhood-level capture rates ($P_{ij}$).
* **Fallback Strategy:** If a user selects a business category not found in this file, the system should default to **Alpha = 1.0** and **Beta = 1.0** (Neutral Gravity).
* **Data Quality Warning:** If a selected category has a correlation near 0.25, the AI should advise the user that the "Size and Distance" factors are moderately predictive, but other factors (like brand power) may apply.
* **Fallback:** If no match is found, revert to the "Neutral Gravity" baseline ($\alpha=1.0, \beta=1.0$).
* **Interpretability:** When the AI provides a recommendation, it should reference these values to explain its reasoning (e.g., *"Because this is a Medical Office with a high Beta of 3.0, proximity to residents is the primary driver of your 15% market share."*)


### **Implementation Guidelines**
1.  **Coordinate System:** All latitude/longitude values are in WGS84 Decimal Degrees.
2.  **Join Logic:** Use `cbg` (or `GEOID10`) to link demographics to spatial shapes. Use `placekey` to link businesses to distance and visit tables.
3.  **New Sites:** When a user drops a new pin (Site X), treat it as a **New Competitor**. Your backend must calculate its distance to all CBGs in real-time to compare against the existing distance matrix.
