Comparison skill for stock similarity selection.

Use this only after the input stock has already been confirmed and standardized.

Goal:
- Accept the confirmed standardized company name and ticker symbol from the prior step.
- Identify meaningful comparable publicly traded stocks for later analysis.

Selection target:
- Return 5 comparable stocks when possible.
- Return fewer only if strong matches are not available.

Allowed similarity criteria:
- Same sector
- Same industry
- Similar business model
- Substitutes or alternatives
- Adjacent players in the market
- Similar profitability profile

Profitability profile means, when available:
- Gross margin
- Operating margin
- Net margin
- ROE
- ROA

Selection rules:
- Prioritize same industry first.
- Then prioritize similar business model.
- Then substitutes or direct alternatives.
- Then adjacent players only if they are still meaningfully related.
- Prefer publicly traded companies with active listings.
- Exclude exact duplicates of the same company.
- Exclude companies that are clearly unrelated in sector, industry, business model, or financial profile.
- Exclude ETFs, funds, indexes, and private companies.
- Do not retrieve ESG data in this step.
- Do not calculate stock correlation in this step.
- Do not generate charts or final reports in this step.

Fetch rules:
- For current company, market, or web information, never answer from memory. Fetch first.
- Prefer structured sources first.
- Use the same source priority as the intake step unless explicitly overridden.
- If using web or news pages, prefer:
  curl -L -A "Mozilla/5.0" "<url>"

Output rules:
- Clean and standardize the comparable-stock result.
- Output only:
  - the confirmed stock
  - the selected comparable stocks
  - the similarity factors justifying each match
- Keep the output concise and structured for the next stage.
- Use this exact schema:

confirmed_stock:
- company_name:
- ticker:

comparable_stocks:
- company_name:
  ticker:
  similarity_factors:
- company_name:
  ticker:
  similarity_factors:

Ranking rules:
- Rank stronger direct comparables above weaker adjacent names.
- Prefer companies matching multiple criteria over companies matching only one criterion.
- If two candidates are similar, prefer the one with closer industry and profitability profile.

Failure rules:
- If no strong comparables are found, return the best available smaller set and state that only limited strong matches were found.
- If the confirmed stock is missing or ambiguous, stop and ask for the confirmed standardized stock input first.