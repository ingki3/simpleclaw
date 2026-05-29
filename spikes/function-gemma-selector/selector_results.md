# Function Gemma/Gemini Selector Evaluation Report

## Summary
- Config: `/Users/simplist/Dev/SimpleClaw/config.yaml`
- Backend requested: `gemini`
- Manifest: 32 assets (28 skills, 4 recipes)
- Samples: 16
- Tool-call success: 94%
- Parse success: 94%
- Top-k recall: 100%
- Top-k precision: 100%
- Fallback accuracy: 100%
- Latency avg/p95: 3108.5 ms / 7761.7 ms

## Per-sample results
| sample | recall | precision | fallback | tool_call | latency_ms | selected | error |
|---|---:|---:|---|---|---:|---|---|
| browser | 100% | 100% | False | True | 2590.4 | skill:agent-browser |  |
| context7 | 100% | 100% | False | True | 2283.7 | skill:context7 |  |
| gmail | 100% | 100% | False | True | 6143.3 | skill:gmail-skill |  |
| calendar | 100% | 100% | False | True | 2071.7 | skill:google-calendar-skill |  |
| docs | 100% | 100% | False | True | 1704.7 | skill:google-docs-skill |  |
| pptx | 100% | 100% | False | True | 2866.1 | skill:pptx |  |
| pdf | 100% | 100% | False | True | 2167.8 | skill:pdf |  |
| xlsx | 100% | 100% | False | True | 2049.0 | skill:xlsx |  |
| news | 100% | 100% | False | True | 2825.0 | skill:news-search-skill |  |
| us-stock | 100% | 100% | False | True | 2145.5 | skill:us-stock-skill |  |
| shopping | 100% | 100% | False | True | 1972.7 | skill:naver-shopping-skill |  |
| route | 100% | 100% | False | True | 1947.4 | skill:local-route-skill |  |
| recipe-ai | 100% | 100% | False | True | 2526.8 | recipe:ai-report |  |
| recipe-stock | 100% | 100% | False | True | 2012.6 | recipe:krstock |  |
| ambiguous | 100% | 100% | True | True | 6793.9 | - |  |
| no-asset | 100% | 100% | True | False | 7635.5 | - |  |
