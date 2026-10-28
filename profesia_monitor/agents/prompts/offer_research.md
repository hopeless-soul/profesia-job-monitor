# Offer research — reading the job offer

How to research the offer link you are given.

## Inputs
- The offer URL.

## Steps
1. Fetch the offer page with `fetch_offer`.
2. Extract: role title, seniority, stack and tools, must-haves vs nice-to-haves,
   responsibilities, company domain, language of the posting, tone.
3. Beyond the posting: only if the company's domain or size is unclear from the
   posting, fetch the company's own website once. Then stop: no further pages,
   no review sites.

## Output
No file. Keep the research in your working notes. It feeds the verdict report
(`content`, Step 4 of your duties) and must be concrete enough to rate every
must-have requirement against the backstory.
