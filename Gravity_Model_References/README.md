# Huff Gravity Model: Market Share Estimation

The Huff gravity model is arguably the most famous probabilistic model in the spatial marketing domain. 
This model and its variants are widely used in empirical studies to estimate market share in competitive environments, primarily to determine optimal locations for new facilities or to assess retail store performance.

## Overview

Originally proposed by David L. Huff (1964), the model estimates the probability of a customer $i$ choosing store $j$ based on a utility value. 
This utility is proportional to the store's **attractiveness** ($A_j$) and inversely proportional to the **customer-store distance** ($D_{ij}$).

### Utility Function
The parameters $\alpha$ and $\beta$ are used to adjust the sensitivity of the model to these two factors:

$$U_{ij} = \frac{A_j^\alpha}{D_{ij}^\beta}$$

### Probability Calculation
The probability $P_{ij}$ that a customer $i$ visits store $j$ is calculated as follows, where $J$ denotes the set of all potential stores:

$$P_{ij} = \frac{U_{ij}}{\sum_{j' \in J}U_{ij'}}$$

## Key Considerations
*   **Attractiveness:** The original model primarily relies on the **floor area** of a facility to determine its attractiveness.
*   **Distance:** The **Euclidean distance** between the customer base and the store location is typically used as an approximation of proximity.

## References
For more detailed information, please refer to the papers available in this folder.
