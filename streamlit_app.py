import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Fantasy League Analytics",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

league_id = str(st.secrets["SLEEPER_LEAGUE_ID"])
API_BASE = "https://api.sleeper.app/v1"

PROJECTIONS_BASE = "https://api.sleeper.app/projections/nfl"
PROJECTION_FIELDS = {1.0: "pts_ppr", 0.5: "pts_half_ppr", 0.0: "pts_std"}

@st.cache_data(ttl=300, show_spinner=False)
def load_league(league_id, first_week, last_week):
    with requests.Session() as session:
        def fetch(endpoint):
            response = session.get(
                f"{API_BASE}/{endpoint}", timeout=30
            )
            response.raise_for_status()
            return response.json()

        league = fetch(f"league/{league_id}")
        if not league:
            raise ValueError("League not found. Check the league ID.")

        users = fetch(f"league/{league_id}/users")
        rosters = fetch(f"league/{league_id}/rosters")

        user_names = {
            user["user_id"]: user.get("display_name", user["user_id"])
            for user in users
        }
        roster_names = {
            roster["roster_id"]: user_names.get(
                roster.get("owner_id"),
                f"Roster {roster['roster_id']}",
            )
            for roster in rosters
        }

        rows = []
        for week in range(first_week, last_week + 1):
            matchups = fetch(f"league/{league_id}/matchups/{week}")
            for team in matchups or []:
                points = team.get("custom_points")
                if points is None:
                    points = team.get("points")

                rows.append({
                    "week": week,
                    "roster_id": team["roster_id"],
                    "display_name": roster_names.get(
                        team["roster_id"], f"Roster {team['roster_id']}"
                    ),
                    "matchup_id": team.get("matchup_id"),
                    "points_scored": points,
                })

    return league, pd.DataFrame(rows)

@st.cache_data(ttl=300, show_spinner=False)
def load_starters(league_id, first_week, last_week):
    """One row per starter per team per week, with actual points."""
    rows = []
    with requests.Session() as session:
        for week in range(first_week, last_week + 1):
            response = session.get(
                f"{API_BASE}/league/{league_id}/matchups/{week}", timeout=30
            )
            response.raise_for_status()
            for team in response.json() or []:
                starters = team.get("starters") or []
                points = team.get("starters_points") or []
                for player_id, actual in zip(starters, points):
                    if player_id in (None, "0"):
                        continue  # empty lineup slot
                    rows.append({
                        "week": week,
                        "roster_id": team["roster_id"],
                        "player_id": str(player_id),
                        "actual_points": actual,
                    })
    return pd.DataFrame(rows)


@st.cache_data(ttl=3600, show_spinner=False)
def load_projections(season, first_week, last_week, scoring_field):
    """One row per player per week, with projected points."""
    params = [("season_type", "regular")] + [
        ("position[]", p) for p in ["QB", "RB", "WR", "TE", "K", "DEF"]
    ]
    rows = []
    with requests.Session() as session:
        for week in range(first_week, last_week + 1):
            response = session.get(
                f"{PROJECTIONS_BASE}/{season}/{week}",
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            entries = data if isinstance(data, list) else list(data.values())
            for entry in entries:
                if not entry:
                    continue
                info = entry.get("player") or {}
                stats = entry.get("stats") or {}
                rows.append({
                    "week": week,
                    "player_id": str(entry.get("player_id")),
                    "player_name": (
                        f"{info.get('first_name', '')} "
                        f"{info.get('last_name', '')}"
                    ).strip(),
                    "position": info.get("position"),
                    "projected_points": stats.get(scoring_field) or 0.0,
                })
    return pd.DataFrame(rows).drop_duplicates(["week", "player_id"])

def calculate_analytics(raw):
    df = raw.copy()
    df["points_scored"] = pd.to_numeric(
        df["points_scored"], errors="coerce"
    )

    # Find each team's scheduled opponent.
    opponents = df[[
        "week", "matchup_id", "roster_id",
        "display_name", "points_scored",
    ]].rename(columns={
        "roster_id": "opponent_roster_id",
        "display_name": "opponent_display_name",
        "points_scored": "opponent_points",
    })

    paired = df.loc[df["matchup_id"].notna()].merge(
        opponents.loc[opponents["matchup_id"].notna()],
        on=["week", "matchup_id"],
    )
    paired = paired.loc[
        paired["roster_id"] != paired["opponent_roster_id"]
    ]

    if paired.duplicated(["week", "roster_id"]).any():
        raise ValueError(
            "A matchup contains more than two teams. "
            "This app supports head-to-head matchups."
        )

    df = df.merge(
        paired[[
            "week", "roster_id", "opponent_display_name",
            "opponent_points",
        ]],
        on=["week", "roster_id"],
        how="left",
        validate="one_to_one",
    )

    # All-play win credit: beat = 1, tie = 0.5, lose = 0.
    scores = df.groupby("week")["points_scored"]
    team_count = scores.transform("count")
    average_rank = scores.rank(method="average", ascending=True)

    df["analytical_wins"] = (
        (average_rank - 1)
        / (team_count - 1).where(team_count > 1)
    )

    valid = (
        df["points_scored"].notna()
        & df["opponent_points"].notna()
        & df["analytical_wins"].notna()
    )
    df = df.loc[valid].copy()

    won = df["points_scored"] > df["opponent_points"]
    tied = df["points_scored"] == df["opponent_points"]

    df["actual_win_credit"] = won.astype(float) + 0.5 * tied.astype(float)
    df["result"] = "Loss"
    df.loc[won, "result"] = "Win"
    df.loc[tied, "result"] = "Tie"
    df["weekly_luck"] = (
        df["actual_win_credit"] - df["analytical_wins"]
    )

    summary = (
        df.groupby(["roster_id", "display_name"], as_index=False)
        .agg(
            weeks_played=("week", "nunique"),
            actual_wins=("actual_win_credit", "sum"),
            analytical_wins=("analytical_wins", "sum"),
            total_points=("points_scored", "sum"),
        )
    )

    if summary.empty:
        return df, summary

    summary["luck_coefficient"] = (
        summary["actual_wins"] - summary["analytical_wins"]
    )

    def standings(win_column):
        # Round only for ranking to avoid floating-point tie differences.
        keys = list(zip(
            summary[win_column].round(10),
            summary["total_points"].round(10),
        ))
        counts = pd.Series(keys).value_counts().to_dict()
        ranks = {}
        position = 1
        for key in sorted(counts, reverse=True):
            ranks[key] = position
            position += counts[key]
        return [ranks[key] for key in keys]

    summary["current standing"] = standings("actual_wins")
    summary["theoretical standing"] = standings("analytical_wins")
    summary["standing change"] = (
        summary["current standing"] - summary["theoretical standing"]
    )

    return df, summary.sort_values(
        ["current standing", "display_name"]
    ).reset_index(drop=True)


DUD_RATIO = 0.5  # dud = scored under 50% of projection


def calculate_matchup_luck(weekly, starters, projections):
    """Measure how much each winner benefited from opponent underperformance."""
    players = starters.merge(projections, on=["week", "player_id"], how="left")
    players["projected_points"] = players["projected_points"].fillna(0.0)
    players["actual_points"] = pd.to_numeric(
        players["actual_points"], errors="coerce"
    ).fillna(0.0)
    players["player_name"] = players["player_name"].fillna("").astype(str)

    # Zero-projection starters are manager error, not luck: exclude them.
    counted = players.loc[players["projected_points"] > 0].copy()
    counted["is_dud"] = (
        counted["actual_points"] < DUD_RATIO * counted["projected_points"]
    )
    counted["dud_points"] = (
        counted["projected_points"] - counted["actual_points"]
    ).where(counted["is_dud"], 0.0)

    team_week = counted.groupby(["week", "roster_id"], as_index=False).agg(
        projected_total=("projected_points", "sum"),
        counted_actual=("actual_points", "sum"),
        duds=("is_dud", "sum"),
        dud_points=("dud_points", "sum"),
    )
    dud_names = (
        counted.loc[counted["is_dud"]]
        .groupby(["week", "roster_id"])["player_name"]
        .agg(", ".join)
        .rename("dud_players")
        .reset_index()
    )
    team_week = team_week.merge(dud_names, on=["week", "roster_id"], how="left")

    # Link each team to its opponent that week.
    pairs = weekly[["week", "matchup_id", "roster_id"]]
    opponents = pairs.merge(pairs, on=["week", "matchup_id"], suffixes=("", "_opp"))
    opponents = opponents.loc[
        opponents["roster_id"] != opponents["roster_id_opp"],
        ["week", "roster_id", "roster_id_opp"],
    ]

    # Wins only, with the losing opponent's projection stats attached.
    wins = weekly.loc[weekly["result"] == "Win", [
        "week", "roster_id", "display_name", "opponent_display_name",
        "points_scored", "opponent_points",
    ]].merge(opponents, on=["week", "roster_id"], how="left")

    opp_stats = team_week.rename(columns={
        "roster_id": "roster_id_opp",
        "projected_total": "opp_projected",
        "counted_actual": "opp_counted_actual",
        "duds": "opp_duds",
        "dud_points": "opp_dud_points",
        "dud_players": "opp_dud_players",
    })
    wins = wins.merge(opp_stats, on=["week", "roster_id_opp"], how="left")
    for column in ["opp_projected", "opp_counted_actual", "opp_duds", "opp_dud_points"]:
        wins[column] = wins[column].fillna(0.0)
    wins["opp_dud_players"] = wins["opp_dud_players"].fillna("")

    # Shortfall: negative (opponent beat projection) counts as 0 luck.
    raw_shortfall = wins["opp_projected"] - wins["opp_counted_actual"]
    wins["shortfall"] = raw_shortfall.clip(lower=0)

    # Stolen win: opponent's expected score would have beaten yours.
    wins["opp_expected"] = wins["opponent_points"] + raw_shortfall
    wins["stolen_win"] = wins["points_scored"] < wins["opp_expected"]

    summary = wins.groupby(["roster_id", "display_name"], as_index=False).agg(
        wins=("week", "count"),
        stolen_wins=("stolen_win", "sum"),
        total_shortfall=("shortfall", "sum"),
        duds_faced=("opp_duds", "sum"),
        dud_points=("opp_dud_points", "sum"),
    )
    summary["avg_shortfall_per_win"] = (
        summary["total_shortfall"] / summary["wins"]
    )
    summary = summary.sort_values(
        ["avg_shortfall_per_win", "stolen_wins"], ascending=False
    ).reset_index(drop=True)

    return wins.sort_values(["week", "display_name"]), summary

st.title("🏈 Who’s Getting the Breaks?")
st.write(
    "Compare actual results with how each manager scored "
    "against the entire league."
)

with st.sidebar:
    st.header("League settings")
    first_week, last_week = st.slider(
        "Weeks to analyze",
        min_value=1,
        max_value=18,
        value=(1, 6),
    )
    st.caption("Choose completed weeks so results reflect final scores.")
    if st.button("Refresh data"):
        load_league.clear()
        load_starters.clear()
        load_projections.clear()

if not league_id.strip().isdigit():
    st.info("Enter a valid numeric Sleeper league ID.")
    st.stop()

try:
    with st.spinner("Loading league data…"):
        league, raw = load_league(
            league_id.strip(), first_week, last_week
        )

    if raw.empty:
        st.info("No matchup data found for these weeks.")
        st.stop()

    weekly, summary = calculate_analytics(raw)

except (requests.RequestException, ValueError, KeyError) as error:
    st.error(f"Unable to load league analytics: {error}")
    st.stop()

if summary.empty:
    st.info("No scored head-to-head matchups found for these weeks.")
    st.stop()
    # Matchup luck data. The projections feed is unofficial, so fail softly.
luck_error = None
try:
    rec = float(league.get("scoring_settings", {}).get("rec", 0))
    scoring_field = PROJECTION_FIELDS.get(rec, "pts_ppr")
    starters = load_starters(league_id.strip(), first_week, last_week)
    projections = load_projections(
        league.get("season"), first_week, last_week, scoring_field
    )
    luck_wins, luck_summary = calculate_matchup_luck(weekly, starters, projections)
except (requests.RequestException, ValueError, KeyError) as error:
    luck_error = error

st.subheader(league.get("name", "League analytics"))
st.caption(
    f"Season {league.get('season', '')} · "
    f"Weeks {first_week}–{last_week} · "
    f"{len(summary)} managers"
)

st.info(
    "**Schedule luck = actual wins − scoring-based wins.** "
    "Positive means more wins than scoring suggests; negative means fewer. "
    "**Standing change:** positive means the manager would move up."
)

standings_tab, luck_tab, matchup_tab, weekly_tab = st.tabs([
    "Standings", "Schedule luck", "Matchup luck", "Weekly results"
])

with standings_tab:
    table = summary.rename(columns={
        "display_name": "Manager",
        "weeks_played": "Weeks played",
        "current standing": "Current standing",
        "theoretical standing": "Theoretical standing",
        "standing change": "Standing change",
        "actual_wins": "Actual wins",
        "analytical_wins": "Scoring-based wins",
        "luck_coefficient": "Schedule luck",
        "total_points": "Total points",
    })[[
        "Manager",
        "Current standing",
        "Theoretical standing",
        "Standing change",
        "Weeks played",
        "Actual wins",
        "Scoring-based wins",
        "Schedule luck",
        "Total points",
    ]]

    limit = max(float(table["Schedule luck"].abs().max()), 1.0)

    styled = (
        table.style
        .format({
            "Standing change": "{:+.0f}",
            "Actual wins": "{:g}",
            "Scoring-based wins": "{:.2f}",
            "Schedule luck": "{:+.2f}",
            "Total points": "{:.2f}",
        })
        .bar(
            subset=["Schedule luck"],
            align="zero",
            vmin=-limit,
            vmax=limit,
            color=["#f4a582", "#92c5de"],
        )
    )

    mobile_columns = [
    "Manager",
    "Current standing",
    "Theoretical standing",
    "Standing change",
    "Schedule luck",
    ]

    compact_table = table[mobile_columns].rename(columns={
        "Current standing": "Actual rank",
        "Theoretical standing": "Scoring rank",
        "Standing change": "Rank change",
        "Schedule luck": "Luck",
    })

    st.dataframe(
        compact_table.style.format({
            "Rank change": "{:+.0f}",
            "Luck": "{:+.2f}",
        }),
        hide_index=True,
        use_container_width=True,
    )

    st.caption(
        "Positive rank change = would move up. "
        "Positive luck = more wins than scoring suggests. "
        "Swipe horizontally to see all columns."
    )

with matchup_tab:
    import altair as alt

    if luck_error is not None:
        st.warning(f"Matchup luck is unavailable right now: {luck_error}")
    else:
        st.write(
            "How much each manager's **wins** were helped by opponents "
            "falling short of their projections."
        )

        # Leaderboard: every manager, including those with no wins yet.
        leaderboard = summary[["roster_id", "display_name"]].merge(
            luck_summary, on=["roster_id", "display_name"], how="left"
        )
        count_columns = ["wins", "stolen_wins", "duds_faced"]
        leaderboard[count_columns] = leaderboard[count_columns].fillna(0).astype(int)
        point_columns = ["total_shortfall", "dud_points"]
        leaderboard[point_columns] = leaderboard[point_columns].fillna(0.0)
        leaderboard = leaderboard.sort_values(
            "avg_shortfall_per_win", ascending=False, na_position="last"
        ).reset_index(drop=True)

        board = leaderboard.rename(columns={
            "display_name": "Manager",
            "wins": "Wins",
            "avg_shortfall_per_win": "Avg shortfall per win",
            "stolen_wins": "Stolen wins",
            "duds_faced": "Duds faced",
            "dud_points": "Dud points",
            "total_shortfall": "Total shortfall",
        })[[
            "Manager", "Wins", "Avg shortfall per win", "Stolen wins",
            "Duds faced", "Dud points", "Total shortfall",
        ]]

        st.subheader("Luck leaderboard")
        st.dataframe(
            board.style.format({
                "Avg shortfall per win": lambda v: "No wins" if pd.isna(v) else f"{v:.2f}",
                "Dud points": "{:.2f}",
                "Total shortfall": "{:.2f}",
            }),
            hide_index=True,
            use_container_width=True,
        )

        # Chart: managers with at least one win.
        chart_data = board.loc[board["Wins"] > 0]
        if not chart_data.empty:
            chart = alt.Chart(chart_data).mark_bar(color="#4393c3").encode(
                x=alt.X(
                    "Avg shortfall per win:Q",
                    title="Average opponent shortfall per win (points)",
                ),
                y=alt.Y("Manager:N", sort="-x", title=None),
                tooltip=[
                    "Manager:N",
                    "Wins:Q",
                    "Stolen wins:Q",
                    alt.Tooltip("Avg shortfall per win:Q", format=".2f"),
                    "Duds faced:Q",
                ],
            )
            st.altair_chart(
                chart.properties(height=max(300, len(chart_data) * 32)),
                use_container_width=True,
            )

        # Shared formatting for the win-level tables.
        details = luck_wins.rename(columns={
            "week": "Week",
            "display_name": "Winner",
            "opponent_display_name": "Opponent",
            "points_scored": "Winner points",
            "opponent_points": "Opponent points",
            "opp_projected": "Opponent projected",
            "shortfall": "Shortfall",
            "stolen_win": "Stolen win",
            "opp_duds": "Duds",
            "opp_dud_points": "Dud points",
            "opp_dud_players": "Dud players",
        })
        details["Stolen win"] = details["Stolen win"].map({True: "Yes", False: ""})
        detail_columns = [
            "Week", "Winner", "Opponent", "Winner points", "Opponent points",
            "Opponent projected", "Shortfall", "Stolen win", "Duds",
            "Dud points", "Dud players",
        ]
        detail_format = {
            "Winner points": "{:.2f}",
            "Opponent points": "{:.2f}",
            "Opponent projected": "{:.2f}",
            "Shortfall": "{:.2f}",
            "Duds": "{:.0f}",
            "Dud points": "{:.2f}",
        }

        st.subheader("Luckiest wins")
        top_wins = details.sort_values("Shortfall", ascending=False).head(10)
        st.dataframe(
            top_wins[detail_columns].style.format(detail_format),
            hide_index=True,
            use_container_width=True,
        )

        st.subheader("Wins by manager")
        selected_luck = st.selectbox(
            "Manager",
            options=leaderboard["display_name"].tolist(),
            key="matchup_luck_manager",
        )
        manager_wins = details.loc[
            details["Winner"] == selected_luck
        ].sort_values("Week")
        if manager_wins.empty:
            st.info(f"{selected_luck} has no wins in the selected weeks.")
        else:
            st.dataframe(
                manager_wins[detail_columns].style.format(detail_format),
                hide_index=True,
                use_container_width=True,
            )

        with st.expander("How matchup luck works"):
            st.markdown("""
- **Wins only:** for every win, we look at the losing opponent's starters.
- **Opponent projected:** the sum of the opponent's starter projections
  (Sleeper projections, matched to your league's scoring).
- **Shortfall:** opponent projected minus opponent actual. If the opponent
  beat their projection, shortfall is 0.
- **Avg shortfall per win:** total shortfall divided by wins. This is the ranking.
- **Stolen win:** you won, but your opponent would have beaten your score
  if their starters had hit their projections.
- **Dud:** an opposing starter who scored under 50% of their projection.
  **Dud points** is how far those duds fell short in total.
- **Zero-projection starters** (like a player on bye or ruled out) are
  excluded. Starting them is manager error, not luck for the winner.
""")
    with st.expander("View detailed statistics"):
        st.dataframe(
            styled,
            hide_index=True,
            use_container_width=True,
        )
    st.caption(
        "Current standing ranks actual win credit, then total points, "
        "for the selected weeks. Theoretical standing ranks scoring-based "
        "wins, then total points. These may differ from Sleeper's live "
        "standings or league-specific tiebreakers."
    )

    st.download_button(
        "Download standings CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"sleeper_standings_weeks_{first_week}_{last_week}.csv",
        mime="text/csv",
    )

with luck_tab:
    import altair as alt

    chart_data = summary.rename(columns={
        "display_name": "Manager",
        "luck_coefficient": "Schedule luck",
        "actual_wins": "Actual wins",
        "analytical_wins": "Scoring-based wins",
    })

    bars = alt.Chart(chart_data).mark_bar().encode(
        x=alt.X(
            "Schedule luck:Q",
            title="Wins above or below scoring-based expectation",
        ),
        y=alt.Y(
            "Manager:N",
            sort=alt.SortField(field="Schedule luck", order="descending"),
            title=None,
        ),
        color=alt.condition(
            alt.datum["Schedule luck"] >= 0,
            alt.value("#4393c3"),
            alt.value("#d6604d"),
        ),
        tooltip=[
            "Manager:N",
            alt.Tooltip("Actual wins:Q", format=".1f"),
            alt.Tooltip("Scoring-based wins:Q", format=".2f"),
            alt.Tooltip("Schedule luck:Q", format="+.2f"),
        ],
    )
    zero = alt.Chart(pd.DataFrame({"zero": [0]})).mark_rule(
        color="#888"
    ).encode(x="zero:Q")

    st.altair_chart(
        (bars + zero).properties(height=max(300, len(summary) * 32)),
        use_container_width=True,
    )

with weekly_tab:
    manager_names = summary.set_index("roster_id")["display_name"].to_dict()
    selected = st.selectbox(
        "Manager",
        options=summary["roster_id"].tolist(),
        format_func=lambda roster_id: manager_names[roster_id],
    )

    manager_weeks = weekly.loc[
        weekly["roster_id"] == selected
    ].sort_values("week")

    weekly_table = manager_weeks.rename(columns={
        "week": "Week",
        "opponent_display_name": "Opponent",
        "points_scored": "Points scored",
        "opponent_points": "Opponent points",
        "result": "Result",
        "analytical_wins": "Scoring-based wins",
        "weekly_luck": "Weekly luck",
    })[[
        "Week", "Opponent", "Points scored", "Opponent points",
        "Result", "Scoring-based wins", "Weekly luck",
    ]]

    st.dataframe(
        weekly_table.style.format({
            "Points scored": "{:.2f}",
            "Opponent points": "{:.2f}",
            "Scoring-based wins": "{:.2f}",
            "Weekly luck": "{:+.2f}",
        }),
        hide_index=True,
        use_container_width=True,
    )

with st.expander("How the calculations work"):
    st.markdown("""
- **Scoring-based wins:** the fraction of other teams a manager
  would have beaten that week. Tied scores count as half a win.
  Outscoring 8 of 11 opponents earns **8 ÷ 11 = 0.73** wins.
- **Actual wins:** a scheduled win earns 1; a tie earns 0.5.
- **Schedule luck:** actual wins minus scoring-based wins.
- **Theoretical standing:** ranking by total scoring-based wins.
- **Standing change:** current standing minus theoretical standing.
  Moving from 8th to 3rd is **+5**.

Only rows with scores for both scheduled opponents are included.
Scores alone do not indicate that a week has finished; select completed weeks.
""")

