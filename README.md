# Cycling VL Report

A static single-page app built with Vue.js and Bootstrap that queries the [cycling.vlaanderen](https://cycling.vlaanderen) API to show race results for a selected team within a chosen date range. Results can be exported to Excel.

**Live:** https://ThomasDekeyser.github.io/cyclingVlReport/static/index.html

---

## Local setup with Hugo
A local running web server is required to bypass CORS issues when fetching data from the cycling.vlaanderen API using corsproxy.io   
Hugo's built-in server is a convenient option for this.
### Prerequisites

- [Hugo](https://gohugo.io/) extended edition (v0.100+)

```bash
brew install hugo
```

### Run locally

```bash
git clone https://github.com/ThomasDekeyser/cyclingVlReport.git
cd cyclingVlReport
hugo server
```

The app is then available at http://localhost:1313/index.html.

---

## API

All requests are routed through [corsproxy.io](https://corsproxy.io) to work around the missing `Access-Control-Allow-Origin` header on the cycling.vlaanderen API.

### 1. Race list

Fetch races within a date range:

```
GET https://cycling.vlaanderen/actions/cycling-api-module/api
  ?method=races.json
  &from_date=YYYY-MM-DD
  &to_date=YYYY-MM-DD
  &provinces=
  &categories=
```

Returns a list of races. Only races with `result = 1` (i.e. results have been submitted) are used.

### 2. Race results

Fetch detailed results for a single race:

```
GET https://cycling.vlaanderen/actions/cycling-api-module/api
  ?method=race_results.json
  &race_id={id}
```

Returns `race_results`, each containing `race_result_lines`. Lines are filtered by `team` matching the selected team name. Displayed fields: place, first name, last name, UCI code, team, club, time.
