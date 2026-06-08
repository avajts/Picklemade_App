"""
app.py
------
Streamlit UI for the Picklemadee.

Flow:
    Phase 1 — Setup:   input courts, rounds, players, couples
    Phase 2 — Results: view schedule, sit-outs, and stats
"""

import streamlit as st
import pandas as pd
from models import Player, ScheduleConfig
from scheduler import build_schedule
from utils import validate_config

# ─────────────────────────────────────────────
#  Page config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Picklemade",
    page_icon="🏓",
    layout="wide",
)

st.title("🏓 Picklemade")
st.caption("Mixed doubles scheduler — fair rotations, couple-friendly.")

# ─────────────────────────────────────────────
#  Session state initialization
# ─────────────────────────────────────────────
if "players"       not in st.session_state:
    st.session_state.players = []        # list of dicts: {name, gender, couple_partner}
if "schedule"      not in st.session_state:
    st.session_state.schedule = None     # list of Round objects
if "warnings"      not in st.session_state:
    st.session_state.warnings = []
if "tracker"       not in st.session_state:
    st.session_state.tracker = None
if "sit_summary"   not in st.session_state:
    st.session_state.sit_summary = {}


# ─────────────────────────────────────────────
#  Helper — build Player objects from session
# ─────────────────────────────────────────────
def session_players() -> list[Player]:
    return [
        Player(
            name=p["name"],
            gender=p["gender"],
            couple_partner=p["couple_partner"] or None,
        )
        for p in st.session_state.players
    ]


# ─────────────────────────────────────────────
#  Sidebar — Setup
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Setup")

    num_courts = st.number_input("Number of courts", min_value=1, max_value=20, value=3)
    num_rounds = st.number_input("Number of rounds", min_value=1, max_value=50, value=8)

    st.divider()

    # ── Add player form ──────────────────────
    st.subheader("👤 Add Player")
    col_name, col_gender = st.columns([3, 1])
    new_name   = col_name.text_input("Name", placeholder="e.g. Alice", label_visibility="collapsed")
    new_gender = col_gender.selectbox("Gender", ["F", "M"], label_visibility="collapsed")

    existing_names = [p["name"] for p in st.session_state.players]
    couple_options = ["None"] + existing_names
    new_couple = st.selectbox(
        "Coupled with (optional)",
        couple_options,
        help="Select an existing player this person is coupled with.",
    )
    new_couple_rounds = 0
    if new_couple != "None":
        new_couple_rounds = st.number_input(
            f"Rounds together",
            min_value=1, max_value=int(num_rounds), value=min(4, int(num_rounds))
        )

    if st.button("➕ Add Player", type="primary"):
        name = new_name.strip()
        if not name:
            st.warning("Please enter a player name.")
        elif name in existing_names:
            st.warning(f"'{name}' is already in the list.")
        else:
            partner = new_couple if new_couple != "None" else None
            st.session_state.players.append({
                "name":           name,
                "gender":         new_gender,
                "couple_partner": partner,
                "couple_rounds":  new_couple_rounds if partner else 0,
            })
            if partner:
                for p in st.session_state.players:
                    if p["name"] == partner:
                        p["couple_partner"] = name
                        p["couple_rounds"]  = new_couple_rounds
            st.success(f"Added {name}!")
            st.rerun()

    st.divider()

    # ── Player list ──────────────────────────
    if st.session_state.players:
        st.subheader("📋 Player List")
        for i, p in enumerate(st.session_state.players):
            c1, c2 = st.columns([5, 1])
            gender_icon = "👩" if p["gender"] == "F" else "👨"
            couple_tag  = f" 💑 {p['couple_partner']}" if p["couple_partner"] else ""
            c1.markdown(f"{gender_icon} **{p['name']}**{couple_tag}")
            if c2.button("❌", key=f"del_{i}"):
                removed = st.session_state.players.pop(i)
                if removed["couple_partner"]:
                    for q in st.session_state.players:
                        if q["name"] == removed["couple_partner"]:
                            q["couple_partner"] = None
                            q["couple_rounds"]  = 0
                st.rerun()

        male_count   = sum(1 for p in st.session_state.players if p["gender"] == "M")
        female_count = sum(1 for p in st.session_state.players if p["gender"] == "F")
        st.caption(f"Total: {len(st.session_state.players)} players — 👨 {male_count}M / 👩 {female_count}F")

    st.divider()

    # ── Generate + Reset buttons ─────────────
    if st.button("🎲 Generate Schedule", type="primary", use_container_width=True):
        players = session_players()

        couple_rounds = {}
        seen = set()
        for p in st.session_state.players:
            if p["couple_partner"] and p["couple_rounds"] > 0:
                key = tuple(sorted([p["name"], p["couple_partner"]]))
                if key not in seen:
                    couple_rounds[key] = p["couple_rounds"]
                    seen.add(key)

        config = ScheduleConfig(
            num_courts=int(num_courts),
            num_rounds=int(num_rounds),
            players=players,
            couple_rounds=couple_rounds,
        )

        errors = validate_config(config)
        if errors:
            for e in errors:
                st.error(e)
        else:
            with st.spinner("Building schedule..."):
                from constraints import ConstraintTracker
                from utils import CoupleScheduler

                rounds, warnings = build_schedule(config)

                # Rebuild tracker for stats display
                cs      = CoupleScheduler(config)
                cs.assign_rounds()
                tracker = ConstraintTracker(config, cs)
                for r in rounds:
                    tracker.update_round(r.courts)

                sit_summary = {}
                for r in rounds:
                    for p in r.sit_outs:
                        sit_summary[p.name] = sit_summary.get(p.name, 0) + 1
                for pl in players:
                    if pl.name not in sit_summary:
                        sit_summary[pl.name] = 0

                st.session_state.schedule    = rounds
                st.session_state.warnings    = warnings
                st.session_state.tracker     = tracker
                st.session_state.sit_summary = sit_summary
            st.success("Schedule ready!")

    if st.button("🗑️ Reset", type="secondary", use_container_width=True):
        for key in ["players", "schedule", "warnings", "tracker", "sit_summary"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()


# ─────────────────────────────────────────────
#  Main area
# ─────────────────────────────────────────────
if st.session_state.schedule is None:
    st.info("👈 Add players and hit **Generate Schedule** to get started.")
    st.markdown("""
    **How it works:**
    - Each court plays **mixed doubles** (1M + 1F per team) where possible
    - Players rotate so they **don't repeat partners or opponents** too often
    - Couples can be assigned extra rounds together
    - Sit-outs rotate fairly if player count doesn't divide evenly into courts
    """)
    st.stop()

# ── Warnings ─────────────────────────────────
if st.session_state.warnings:
    with st.expander("⚠️ Scheduler Warnings", expanded=True):
        for w in st.session_state.warnings:
            st.warning(w)

# ── Tabs ─────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📅 Schedule", "🏆 Stats", "📊 Pair Matrix"])

# ═══════════════════════════════════════════
#  Tab 1 — Schedule
# ═══════════════════════════════════════════
with tab1:
    rounds = st.session_state.schedule

    round_nums  = [f"Round {r.round_num}" for r in rounds]
    view_all    = st.checkbox("Show all rounds", value=True)

    if view_all:
        selected_rounds = rounds
    else:
        chosen = st.selectbox("Select round", round_nums)
        idx    = round_nums.index(chosen)
        selected_rounds = [rounds[idx]]

    for r in selected_rounds:
        sit_names = [p.name for p in r.sit_outs]
        header    = f"**Round {r.round_num}**"
        if sit_names:
            header += f"  —  💺 Sit-out: {', '.join(sit_names)}"

        with st.expander(header, expanded=True):
            for court in r.courts:
                def gender_badge(player: Player) -> str:
                    icon = "👩" if player.gender == "F" else "👨"
                    return f"{icon} {player.name}"

                t1 = court.team1
                t2 = court.team2
                t1_str    = " & ".join(gender_badge(p) for p in t1.players)
                t2_str    = " & ".join(gender_badge(p) for p in t2.players)
                warn_t1   = " ⚠️ same gender" if not t1.is_mixed else ""
                warn_t2   = " ⚠️ same gender" if not t2.is_mixed else ""

                c1, c2, c3 = st.columns([5, 1, 5])
                c1.markdown(f"**{t1_str}**{warn_t1}")
                c2.markdown(
                    "<div style='text-align:center;font-size:1.2rem;padding-top:4px'>vs</div>",
                    unsafe_allow_html=True,
                )
                c3.markdown(f"**{t2_str}**{warn_t2}")
                st.caption(f"Court {court.court_num}")
                st.divider()

# ═══════════════════════════════════════════
#  Tab 2 — Stats
# ═══════════════════════════════════════════
with tab2:
    tracker = st.session_state.tracker

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Rounds",         len(st.session_state.schedule))
    col2.metric("Max Partner Repeats",  tracker.max_partner_repeats())
    col3.metric("Max Opponent Repeats", tracker.max_opponent_repeats())

    st.divider()

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("💺 Sit-Out Counts")
        sit_df = pd.DataFrame([
            {"Player": k, "Times Sat Out": v}
            for k, v in sorted(st.session_state.sit_summary.items(), key=lambda x: -x[1])
        ])
        st.dataframe(sit_df, use_container_width=True, hide_index=True)

    with col_b:
        st.subheader("🤝 Partner Repeats")
        partner_data = tracker.partner_summary()
        if partner_data:
            st.dataframe(pd.DataFrame(partner_data), use_container_width=True, hide_index=True)
        else:
            st.info("No repeat partners.")

    st.subheader("⚔️ Opponent Repeats")
    opp_data = tracker.opponent_summary()
    if opp_data:
        st.dataframe(pd.DataFrame(opp_data), use_container_width=True, hide_index=True)
    else:
        st.info("No repeat opponents.")

# ═══════════════════════════════════════════
#  Tab 3 — Pair Matrix
# ═══════════════════════════════════════════
with tab3:
    tracker = st.session_state.tracker
    players = session_players()
    names   = [p.name for p in players]

    st.subheader("🤝 Partner Count Matrix")
    st.caption("How many times each pair has been partners. Diagonal = N/A.")

    matrix = []
    for a in names:
        row = []
        for b in names:
            row.append("—" if a == b else tracker.partner_count[a][b])
        matrix.append(row)

    st.dataframe(
        pd.DataFrame(matrix, index=names, columns=names),
        use_container_width=True,
    )

    st.divider()

    st.subheader("⚔️ Opponent Count Matrix")
    st.caption("How many times each pair has faced each other as opponents.")

    opp_matrix = []
    for a in names:
        row = []
        for b in names:
            row.append("—" if a == b else tracker.opponent_count[a][b])
        opp_matrix.append(row)

    st.dataframe(
        pd.DataFrame(opp_matrix, index=names, columns=names),
        use_container_width=True,
    )

# ─────────────────────────────────────────────
#  Footer
# ─────────────────────────────────────────────
st.markdown("---")
st.caption("Picklemade · Built with Streamlit 🏓")