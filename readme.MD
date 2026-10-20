# 🏈 Sleeper League Analytics

A Streamlit dashboard that compares fantasy football managers’ actual results with their scoring performance against the entire league.

See who has benefited from favorable matchups, who has been unlucky, and how the standings would look based on scoring-based wins.

## Features

- Select a range of weeks to analyze.
- Compare actual and theoretical standings.
- View schedule luck by manager.
- Explore weekly scores, opponents, and results.
- Download standings as a CSV.
- Refresh league data on demand.

## Metrics

| Metric | Definition |
|---|---|
| Actual wins | Scheduled wins, with ties counting as half a win. |
| Scoring-based wins | The fraction of other teams a manager would have beaten each week, summed across the selected weeks. |
| Schedule luck | Actual wins minus scoring-based wins. Positive means more wins than scoring suggests; negative means fewer. |
| Current standing | Rank by actual win credit, with total points as the tiebreaker. |
| Theoretical standing | Rank by scoring-based wins, with total points as the tiebreaker. |
| Standing change | Current standing minus theoretical standing. Positive means the manager would move up. |

### Scoring-based wins example

In a 12-team league, each manager has 11 possible opponents.

If a manager outscores 8 teams and ties 1 other team:

```text
Scoring-based wins = (8 + 0.5 × 1) / 11
                   = 0.773
```

### Schedule luck example

A manager with 5 actual wins and 3.2 scoring-based wins has:

```text
Schedule luck = 5 − 3.2 = +1.8
```

They have earned 1.8 more wins than their scoring-based results suggest.

### Standing change example

```text
Current standing:      8th
Theoretical standing:  3rd
Standing change:       +5
```

## Project files

```text
sleeper-league-analytics/
├── streamlit_app.py
├── requirements.txt
└── README.md
```

## Run locally

Requires Python 3.10 or newer.

1. Clone the repository and open its directory.
2. Optionally create and activate a virtual environment.
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the app:

   ```bash
   streamlit run streamlit_app.py
   ```

Open the local URL shown in your terminal.

### Dependencies

Add the following to `requirements.txt`:

```text
streamlit
pandas
requests
altair
```

## League configuration

The default league is configured in `streamlit_app.py`:

```python
DEFAULT_LEAGUE_ID = "1380298433323098112"
```

To use another league, change this value or enter its league ID in the sidebar if the league input is enabled.

The league ID is the number in a Sleeper league URL:

```text
https://sleeper.com/leagues/1380298433323098112/matchup
```

## Share the app

Deploy the repository through [Streamlit Community Cloud](https://share.streamlit.io/):

1. Connect your GitHub account.
2. Select the repository and branch.
3. Set the main file to `streamlit_app.py`.
4. Deploy and share the resulting app URL.

## Data source

Data comes from the public, read-only [Sleeper API](https://docs.sleeper.com/). No API key or Sleeper login is required.

The app retrieves:

- League details
- League members
- Rosters
- Weekly matchups and scores

Commissioner-adjusted scores are used when available. API responses are cached for five minutes. The **Refresh data** button clears the cache.

## Interpretation and limitations

- **Select completed weeks.** In-progress or future weeks may contain scores that are not final.
- **Current standing reflects the selected weeks**, rather than necessarily matching Sleeper’s live standings.
- The app ranks by win credit and total points. It does not reproduce division rules, custom tiebreakers, or additional league-median wins.
- Each matchup must contain two teams. Rows without a scored opponent are excluded from season totals.
- Schedule luck measures the difference between scheduled results and all-play scoring performance. It is descriptive, not a prediction of future results.
- Historical results are labeled using the league’s currently returned roster ownership and display names.

This is an independent project and is not affiliated with or endorsed by Sleeper.