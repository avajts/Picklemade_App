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
from models import Player, ScheduleConfig, Team, Round, CourtAssignment
from scheduler import build_schedule
from utils import validate_config
from database import save_session, load_session, save_score, load_scores, get_supabase_client

try:
    _ = st.secrets["SUPABASE_URL"]
    SUPABASE_CONFIGURED = True
except Exception:
    SUPABASE_CONFIGURED = False

# ─────────────────────────────────────────────
#  Page config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Picklemade",
    page_icon="🏓",
    layout="wide",
)

# ── Check for a session code in the URL (e.g. ?session=PICKLE-1K97) ──
query_params = st.query_params
url_session_code = query_params.get("session", None)

st.markdown("""
<style>
    button[kind="secondary"]:has(div:contains("➕")) {
        background-color: #2c2c2c !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] .stTextInput,
    section[data-testid="stSidebar"] .stSelectbox,
    section[data-testid="stSidebar"] .stNumberInput {
        margin-bottom: -12px;
    }
    section[data-testid="stSidebar"] .stForm {
        padding-top: 0px;
    }
    section[data-testid="stSidebar"] hr {
        margin: 8px 0px;
    }
    section[data-testid="stSidebar"] h3 {
        margin-bottom: 4px;
        margin-top: 4px;
    }
</style>
""", unsafe_allow_html=True)

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
if "game_mode" not in st.session_state:
    st.session_state.game_mode = "mixed"


# ─────────────────────────────────────────────
#  Helper — build Player objects from session
# ─────────────────────────────────────────────
def session_players() -> list[Player]:
    return [
        Player(
            name=p["name"],
            gender=p["gender"],
            preferred_partners=p.get("preferred_partners", []),
            avoid_partner=p.get("avoid_partner") or None,
            duper_rating=p.get("duper_rating"),
        )
        for p in st.session_state.players
    ]

def recompute_all_stats(rounds, players, config):
    """
    Rebuilds the constraint tracker and sit-out summary from a (possibly edited)
    schedule. Call this any time the schedule is manually changed.
    """
    from constraints import ConstraintTracker
    from utils import CoupleScheduler

    cs = CoupleScheduler(config)
    cs.assign_rounds()
    tracker = ConstraintTracker(config, cs)

    for r in rounds:
        tracker.update_round(r.courts, r.round_num)

    sit_summary = {}
    for r in rounds:
        for p in r.sit_outs:
            sit_summary[p.name] = sit_summary.get(p.name, 0) + 1
    for pl in players:
        if pl.name not in sit_summary:
            sit_summary[pl.name] = 0

    return tracker, sit_summary

def deserialize_loaded_session(session_data: dict):
    """
    Converts raw Supabase JSON data back into Player/Round/CourtAssignment objects
    so the rest of the app can render it normally.
    """
    config_data   = session_data["config_data"]
    schedule_data = session_data["schedule_data"]

    # Rebuild players
    players = [
        Player(
            name=p["name"],
            gender=p["gender"],
            preferred_partners=[tuple(pp) for pp in p.get("preferred_partners", [])],
            avoid_partner=p.get("avoid_partner"),
            duper_rating=p.get("duper_rating"),
        )
        for p in config_data["players"]
    ]
    player_lookup = {p.name: p for p in players}

    # Rebuild court_overrides
    court_overrides = {}
    for key_str, mode in config_data.get("court_overrides", {}).items():
        r_str, c_str = key_str.split("_")
        court_overrides[(int(r_str), int(c_str))] = mode

    # Rebuild scoring config
    from models import ScoringConfig
    sc_data = config_data.get("scoring_config", {})
    scoring_config = ScoringConfig(
        game_to=sc_data.get("game_to", 11),
        win_by=sc_data.get("win_by", 2),
        scoring_type=sc_data.get("scoring_type", "sideout"),
        time_limit_minutes=sc_data.get("time_limit_minutes"),
    )

    config = ScheduleConfig(
        num_courts=config_data["num_courts"],
        num_rounds=config_data["num_rounds"],
        players=players,
        game_mode=config_data["game_mode"],
        court_overrides=court_overrides,
        scoring_config=scoring_config,
    )

    # Rebuild rounds
    rounds = []
    for r_data in schedule_data:
        courts = []
        for c_data in r_data["courts"]:
            team1_players = [player_lookup[name] for name in c_data["team1"]]
            team2_players = [player_lookup[name] for name in c_data["team2"]]
            courts.append(CourtAssignment(
                court_num=c_data["court_num"],
                team1=Team(team1_players),
                team2=Team(team2_players),
                mode=c_data.get("mode", "mixed"),
            ))
        sit_outs = [player_lookup[name] for name in r_data["sit_outs"] if name in player_lookup]
        rounds.append(Round(round_num=r_data["round_num"], courts=courts, sit_outs=sit_outs))

    return rounds, config, players

# ─────────────────────────────────────────────
#  Sidebar — Setup
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("🔗 Join a Schedule")
    join_code_input = st.text_input(
        "Enter share code",
        value=url_session_code if url_session_code else "",
        placeholder="e.g. PICKLE-1K97",
    )
    if st.button("📥 Load Schedule", use_container_width=True):
        if join_code_input.strip():
            with st.spinner("Loading schedule..."):
                session_data = load_session(join_code_input.strip())
            if session_data is None:
                st.error(f"No schedule found for code '{join_code_input.strip().upper()}'. Check the code and try again.")
            else:
                st.session_state.loaded_session_code = join_code_input.strip().upper()
                st.session_state.loaded_session_data  = session_data
                st.success("Schedule loaded!")
                st.rerun()
        else:
            st.warning("Please enter a code.")

    st.divider()
    st.header("⚙️ Setup")

    # Auto-load if a session code was passed via URL and not already loaded
    if url_session_code and "loaded_session_data" not in st.session_state:
        with st.spinner("Loading shared schedule..."):
            auto_session_data = load_session(url_session_code)
        if auto_session_data:
            st.session_state.loaded_session_code = url_session_code.upper()
            st.session_state.loaded_session_data  = auto_session_data
            st.rerun()
    
    st.subheader("🎮 Game Mode")
    game_mode = st.radio(
        "Select game type",
        options=["mixed", "womens", "mens"],
        format_func=lambda x: {"mixed": "⚧ Mixed Doubles", "womens": "👩 Women's Only", "mens": "👨 Men's Only"}[x],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.session_state.game_mode = game_mode    # ← save to session state
    
    st.divider()

    col_c, col_r = st.columns(2)
    num_courts = col_c.number_input("Courts", min_value=1, max_value=20, value=3)
    num_rounds = col_r.number_input("Rounds", min_value=1, max_value=50, value=8)
    st.divider()

    with st.expander("🏆 Scoring Settings"):
        game_to_choice = st.selectbox("Game format", ["11", "15", "21", "timed"], index=0)
        win_by_choice  = st.selectbox("Win by", [1, 2], index=1)
        scoring_type_choice = st.selectbox("Scoring type", ["sideout", "rally"], index=0)
        time_limit = None
        if game_to_choice == "timed":
            time_limit = st.number_input("Time limit (minutes)", min_value=1, max_value=60, value=12)

    st.divider()

    # ── Per-round court gender overrides ──────
    with st.expander("🎛️ Advanced: Per-Round Court Overrides"):
        st.caption(
            "Override the gender mode for specific courts in specific rounds. "
            "Leave as 'Default' to use the global game mode above."
        )

        if "court_overrides" not in st.session_state:
            st.session_state.court_overrides = {}

        for r in range(1, int(num_rounds) + 1):
            cols = st.columns(int(num_courts) + 1)
            cols[0].markdown(f"**Round {r}**")
            for c in range(1, int(num_courts) + 1):
                key = (r, c)
                current = st.session_state.court_overrides.get(key, "default")
                choice = cols[c].selectbox(
                    f"R{r}C{c}",
                    options=["default", "mixed", "womens", "mens"],
                    index=["default", "mixed", "womens", "mens"].index(current),
                    key=f"override_{r}_{c}",
                    label_visibility="collapsed",
                )
                if choice == "default":
                    st.session_state.court_overrides.pop(key, None)
                else:
                    st.session_state.court_overrides[key] = choice

    # ── Add player form ──────────────────────
    st.markdown("##### 👤 Add Player")

    if "persisted_gender" not in st.session_state:
        st.session_state.persisted_gender = "F"
    if "pending_partners" not in st.session_state:
        st.session_state.pending_partners = []

    if "pending_partners" not in st.session_state:
        st.session_state.pending_partners = []

    # Handle field clearing from the previous run, BEFORE widgets are created
    if st.session_state.get("clear_player_fields", False):
        st.session_state.new_player_name   = ""
        st.session_state.pp_rounds_input    = 1
        st.session_state.new_player_rating  = ""
        st.session_state.new_player_avoid   = "None"
        st.session_state.pp_name_select     = "None"
        st.session_state.clear_player_fields = False

    existing_names = [p["name"] for p in st.session_state.players]

    if game_mode == "mixed":
        col_name, col_gender = st.columns([3, 1])
        form_name = col_name.text_input(
            "Name", placeholder="e.g. Alice", label_visibility="collapsed",
            key="new_player_name",
        )
        form_gender = col_gender.selectbox(
            "Gender", ["F", "M"],
            index=["F", "M"].index(st.session_state.persisted_gender),
            label_visibility="collapsed", key="new_player_gender",
        )
    else:
        form_name = st.text_input(
            "Name", placeholder="e.g. Alice", label_visibility="collapsed",
            key="new_player_name",
        )
        form_gender = "F" if game_mode == "womens" else "M"

    # Preferred Partners — directly under Name, before Avoid
    pp_col1, pp_col2, pp_col3 = st.columns([2, 1, 1])
    pp_name = pp_col1.selectbox(
        "Preferred partners (optional)", ["None"] + existing_names, key="pp_name_select"
    )
    pp_rounds = pp_col2.number_input(
        "Rounds together", min_value=1, max_value=int(num_rounds), value=1,
        key="pp_rounds_input"
    )
    pp_col3.markdown("&nbsp;")  # invisible label-height placeholder, matches real label rendering
    add_partner_clicked = pp_col3.button("➕", key="pp_add_btn", help="Add this preferred partner", type="primary")
    if add_partner_clicked:
        if pp_name != "None" and pp_name not in [n for n, _ in st.session_state.pending_partners]:
            st.session_state.pending_partners.append((pp_name, pp_rounds))
            st.rerun()

    if st.session_state.pending_partners:
        for i, (pname, prounds) in enumerate(st.session_state.pending_partners):
            tag_col1, tag_col2 = st.columns([5, 1])
            tag_col1.caption(f"💑 {pname} — {prounds} rounds")
            if tag_col2.button("✕", key=f"pp_remove_{i}"):
                st.session_state.pending_partners.pop(i)
                st.rerun()

    # Avoid Partner
    avoid_excluded = [n for n, _ in st.session_state.pending_partners]
    avoid_options  = ["None"] + [n for n in existing_names if n not in avoid_excluded]
    form_avoid = st.selectbox(
        "Avoids partnering with",
        avoid_options,
        help="This player will never be placed on the same team as the selected player.",
        key="new_player_avoid",
    )

    # DUPR Rating
    form_rating_raw = st.text_input(
        "DUPR rating (optional)",
        placeholder="e.g. 4.25",
        help="DUPR skill rating from 0.0 to 7.0. Leave blank if unknown.",
        key="new_player_rating",
    )

    # Parse and validate the typed value
    form_rating = None
    if form_rating_raw.strip():
        try:
            parsed = float(form_rating_raw.strip())
            if 0.0 <= parsed <= 7.0:
                form_rating = parsed
            else:
                st.warning("DUPR rating must be between 0.0 and 7.0 — it won't be saved.")
        except ValueError:
            st.warning("DUPR rating must be a number (e.g. 4.25) — it won't be saved.")

    # Submit button
    add_clicked = st.button("➕ Add Player", type="primary", use_container_width=True)

    if add_clicked:
        name = form_name.strip()
        if not name:
            st.warning("Please enter a player name.")
        elif name in existing_names:
            st.warning(f"'{name}' is already in the list.")
        else:
            partners_list = list(st.session_state.pending_partners)

            st.session_state.players.append({
                "name":               name,
                "gender":             form_gender,
                "preferred_partners": partners_list,
                "avoid_partner":      form_avoid if form_avoid != "None" else None,
                "duper_rating":       form_rating,
            })

            for partner_name, rounds in partners_list:
                for p in st.session_state.players:
                    if p["name"] == partner_name:
                        existing = dict(p.get("preferred_partners", []))
                        existing[name] = rounds
                        p["preferred_partners"] = list(existing.items())

            st.session_state.persisted_gender = form_gender
            st.session_state.pending_partners = []
            st.session_state.clear_player_fields = True   # ← flag instead of direct assignment

            st.success(f"Added {name}!")
            st.rerun()

    # ── Player list ──────────────────────────
    if st.session_state.players:
        st.subheader("📋 Player List")
        for i, p in enumerate(st.session_state.players):
            c1, c2 = st.columns([5, 1])
            gender_icon = "👩" if p["gender"] == "F" else "👨"
            partners = p.get("preferred_partners", [])
            couple_tag = ""
            if partners:
                tags = ", ".join(f"{n} ({r}r)" for n, r in partners)
                couple_tag = f" 💑 {tags}"
            avoid_tag   = f" 🚫 {p['avoid_partner']}" if p.get("avoid_partner") else ""
            rating_tag  = f" 🎯 {p['duper_rating']:.2f}" if p.get("duper_rating") else ""
            c1.markdown(f"{gender_icon} **{p['name']}**{couple_tag}{avoid_tag}{rating_tag}")
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
        mode_label = {"mixed": "Mixed Doubles", "womens": "Women's Only", "mens": "Men's Only"}[game_mode]
        if game_mode == "mixed":
            st.caption(f"{mode_label} · {len(st.session_state.players)} players — 👨 {male_count}M / 👩 {female_count}F")
        else:
            st.caption(f"{mode_label} · {len(st.session_state.players)} players")
    
    st.divider()

    # ── Generate + Reset buttons ─────────────
    if st.button("🎲 Generate Schedule", type="primary", use_container_width=True):
        players = session_players()

        from models import ScoringConfig
        scoring_config = ScoringConfig(
            game_to=game_to_choice if game_to_choice == "timed" else int(game_to_choice),
            win_by=win_by_choice,
            scoring_type=scoring_type_choice,
            time_limit_minutes=time_limit,
        )

        config = ScheduleConfig(
            num_courts=int(num_courts),
            num_rounds=int(num_rounds),
            players=players,
            game_mode=st.session_state.get("game_mode", "mixed"),
            court_overrides=st.session_state.get("court_overrides", {}),
            scoring_config=scoring_config,
        )
        st.session_state.last_config = config
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
                    tracker.update_round(r.courts, r.round_num)

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

            if SUPABASE_CONFIGURED:
                try:
                    session_code = save_session(rounds, config)
                    st.session_state.session_code = session_code
                    st.success(f"Schedule ready! Share code: **{session_code}**")
                except Exception as e:
                    st.warning(f"Schedule generated, but couldn't save to shared storage: {e}")
            else:
                st.success("Schedule ready!")

    if st.button("🔧 Test Supabase Connection"):
        try:
            client = get_supabase_client()
            result = client.table("sessions").select("*").limit(1).execute()
            st.success(f"Connected! Found {len(result.data)} existing rows.")
        except Exception as e:
            st.error(f"Connection test failed: {e}")

    if st.button("🗑️ Reset", type="secondary", use_container_width=True):
        for key in ["players", "schedule", "warnings", "tracker", "sit_summary"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

    if st.session_state.get("session_code"):
        st.divider()
        st.markdown("### 🔗 Share This Schedule")
        code = st.session_state.session_code
        st.code(code, language=None)
        share_url = f"https://picklemadeapp.streamlit.app/?session={code}"
        st.text_input("Shareable link", value=share_url, label_visibility="collapsed")
        st.caption("Anyone with this code or link can view the schedule and enter scores.")

# ── If a session was loaded via code/link, populate the normal display state ──
if st.session_state.get("loaded_session_data") and not st.session_state.get("schedule"):
    rounds, config, players = deserialize_loaded_session(st.session_state.loaded_session_data)
    tracker, sit_summary = recompute_all_stats(rounds, players, config)

    st.session_state.schedule     = rounds
    st.session_state.last_config  = config
    st.session_state.tracker      = tracker
    st.session_state.sit_summary  = sit_summary
    st.session_state.warnings     = []
    st.session_state.session_code = st.session_state.loaded_session_code

# ─────────────────────────────────────────────
#  Main area
# ─────────────────────────────────────────────
if st.session_state.schedule is None:
    st.markdown("""
    <div style="text-align:center; padding: 3rem 1rem;">
        <div style="font-size: 3rem;">🏓</div>
        <h2 style="font-family: 'Oswald', sans-serif; letter-spacing: 2px;">Welcome to Picklemade</h2>
         <p style="font-size: 1.1rem; color: #555;">Your mixed doubles pickleball scheduler.</p>
        <br>
        <div style="
            display: inline-block;
            background: #2c2c2c;
            color: white;
            padding: 1rem 2rem;
            border-radius: 8px;
            font-size: 1rem;
            line-height: 1.8;
        ">
            Press the <strong>&gt;</strong><strong>&gt;</strong> arrow in the <strong>top left corner</strong> to open the sidebar.<br>
            Then add your players and hit <strong>Generate Schedule</strong>
        </div>
        <br><br>
        <p style="font-size: 0.9rem; color: #888;">
            ✅ Mixed doubles pairing &nbsp;|&nbsp; 💑 Couple support &nbsp;|&nbsp; 💺 Fair sit-out rotation
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ── Warnings ─────────────────────────────────
if st.session_state.warnings:
    with st.expander("⚠️ Scheduler Warnings", expanded=True):
        for w in st.session_state.warnings:
            st.warning(w)

# ── Tabs ─────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📅 Schedule", "🏆 Stats", "📊 Pair Matrix", "✏️ Edit Schedule"])

# ═══════════════════════════════════════════
#  Tab 1 — Schedule
# ═══════════════════════════════════════════
with tab1:
    config = st.session_state.get("last_config")
    
    if st.session_state.schedule is None:
        st.info("Generate a schedule first.")
    else:
        rounds = st.session_state.schedule

        # ── Print tip ────────────────────────
        st.caption("💡 Tip: Use your browser's Print function (Ctrl+P / Cmd+P) to print this schedule.")

        round_nums = [f"Game {r.round_num}" for r in rounds]
        view_all   = st.checkbox("Show all games", value=True)

        if view_all:
            selected_rounds = rounds
        else:
            chosen = st.selectbox("Select game", round_nums)
            idx    = round_nums.index(chosen)
            selected_rounds = [rounds[idx]]

        # ── Print/display styles ─────────────
        st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Source+Sans+3:wght@400;600&display=swap');

        .game-block {
            margin-bottom: 1rem;      /* was 2rem */
            page-break-inside: avoid;
        }
                    
        .game-header {
            background: #2c2c2c;
            color: white;
            text-align: center;
            padding: 6px 0;          /* was 10px */
            font-family: 'Oswald', sans-serif;
            font-size: 1.2rem;        /* was 1.6rem */
            font-weight: 700;
            letter-spacing: 2px;
            margin-bottom: 0;
        }

        .courts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0;
            border: 2px solid #2c2c2c;
            border-top: none;
        }

        .court-cell {
            border-right: 2px solid #2c2c2c;
            padding: 8px 6px;         /* was 12px 8px */
            text-align: center;
            background: #f5f5f5;
        }
        .court-cell:last-child { border-right: none; }

        .court-title {
            font-family: 'Oswald', sans-serif;
            font-size: 0.95rem;       /* was 1.15rem */
            font-weight: 600;
            color: #2c2c2c;
            background: #dcdcdc;
            margin: -12px -8px 6px -8px;  /* was 8px bottom */
            padding: 4px 0;           /* was 6px */
            letter-spacing: 1px;
        }

        .team-name {
            font-family: 'Source Sans 3', sans-serif;
            font-size: 0.9rem;        /* was 1.05rem */
            font-weight: 600;
            color: #1a1a1a;
            line-height: 1.3;         /* was 1.4 */
        }

        .vs-label {
            font-family: 'Oswald', sans-serif;
            font-size: 0.75rem;       /* was 0.85rem */
            color: #888;
            margin: 2px 0;            /* was 4px */
            letter-spacing: 1px;
        }

        .sitout-bar {
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-top: none;
            text-align: center;
            padding: 4px;
            font-family: 'Source Sans 3', sans-serif;
            font-size: 0.85rem;
            color: #856404;
        }

        .warn-tag {
            font-size: 0.7rem;
            color: #cc4400;
            display: block;
        }

        @media print {
            .stSidebar, .stToolbar, .stDecoration,
            [data-testid="stHeader"], [data-testid="stToolbar"],
            .stCheckbox, .stSelectbox, .element-container:has(.stCheckbox) { display: none !important; }
            .game-block { page-break-inside: avoid; margin-bottom: 1.2rem; }
            .game-header { font-size: 1.3rem; padding: 6px 0; }
            .team-name { font-size: 0.95rem; }
        }
        </style>
        """, unsafe_allow_html=True)

        # ── PDF Export ───────────────────────────
        from fpdf import FPDF
        import io

        def generate_pdf(rounds_to_export):
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=10)
            pdf.add_page()
            pdf.set_margins(10, 10, 10)

            # Website URL
            pdf.set_font("Helvetica", "", 8)
            pdf.set_fill_color(44, 44, 44)
            pdf.set_text_color(180, 180, 180)
            pdf.cell(0, 6, "Visit picklemadeapp.streamlit.app", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

            # Title
            pdf.set_font("Helvetica", "B", 18)
            pdf.set_fill_color(44, 44, 44)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 10, "PICKLEMADE SCHEDULE", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

            for r in rounds_to_export:
                # Game header
                pdf.set_font("Helvetica", "B", 12)
                pdf.set_fill_color(80, 80, 80)
                pdf.set_text_color(255, 255, 255)
                pdf.cell(0, 7, f"  GAME {r.round_num}", fill=True, new_x="LMARGIN", new_y="NEXT")

                # Court columns
                num_courts = len(r.courts)
                page_width = pdf.w - 20          # subtract margins
                col_width  = page_width / num_courts
                row_height = 6

                # Court headers row
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_fill_color(220, 220, 220)
                pdf.set_text_color(30, 30, 30)
                for court in r.courts:
                    mode_icon = {"mixed": "", "womens": " (W)", "mens": " (M)"}.get(court.mode, "")
                    pdf.cell(col_width, row_height, f"Court {court.court_num}{mode_icon}",
                            border=1, align="C", fill=True)
                pdf.ln()

                # Team 1 row
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_fill_color(245, 245, 245)
                for court in r.courts:
                    t1 = " & ".join(p.name for p in court.team1.players)
                    pdf.cell(col_width, row_height, t1, border=1, align="C", fill=True)
                pdf.ln()

                # VS row
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_fill_color(255, 255, 255)
                pdf.set_text_color(120, 120, 120)
                for court in r.courts:
                    pdf.cell(col_width, row_height - 1, "vs", border=1, align="C", fill=True)
                pdf.ln()

                # Team 2 row
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_fill_color(245, 245, 245)
                pdf.set_text_color(30, 30, 30)
                for court in r.courts:
                    t2 = " & ".join(p.name for p in court.team2.players)
                    pdf.cell(col_width, row_height, t2, border=1, align="C", fill=True)
                pdf.ln()

                # Sit-out row
                sit_names = [p.name for p in r.sit_outs]
                if sit_names:
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.set_fill_color(255, 243, 205)
                    pdf.set_text_color(100, 80, 0)
                    pdf.cell(0, row_height - 1, f"  Sitting out: {', '.join(sit_names)}",
                            border=1, align="L", fill=True, new_x="LMARGIN", new_y="NEXT")

                pdf.ln(1)

            return bytes(pdf.output())


        col_export1, col_export2 = st.columns([3, 1])
        col_export1.markdown("### 📅 Game Schedule")
        pdf_bytes = generate_pdf(selected_rounds)
        col_export2.download_button(
            label="⬇️ Download PDF",
            data=pdf_bytes,
            file_name="picklemade_schedule.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

        # ── Render each round ────────────────
        for r in selected_rounds:
            sit_names = [p.name for p in r.sit_outs]
            leftover_for_this_round = next(
                (c.court_num for c in r.courts
                 if c.mode == "mixed" and any(
                     getattr(p, "duper_rating", None) is not None for p in c.all_players
                 )),
                None
            )

            # Build court cells HTML
            cells_html = ""
            for court in r.courts:
                mode_icon = {"mixed": "⚧", "womens": "👩", "mens": "👨"}.get(court.mode, "⚧")   # ← here, inside the loop
                t1 = court.team1
                t2 = court.team2
                t1_str  = " & ".join(p.name for p in t1.players)
                t2_str  = " & ".join(p.name for p in t2.players)
                warn_t1 = " ⚠️ same gender" if (not t1.is_mixed and st.session_state.get("game_mode", "mixed") == "mixed") else ""
                warn_t2 = " ⚠️ same gender" if (not t2.is_mixed and st.session_state.get("game_mode", "mixed") == "mixed") else ""
                
                is_leftover = (court.court_num == leftover_for_this_round and mode_icon == "⚧")
                skill_tag   = " 🎯" if is_leftover else ""
                cells_html += f"""
                <div class="court-cell">
                    <div class="court-title">{mode_icon} Court {court.court_num}{skill_tag}</div>
                    <div class="team-name">{t1_str}{warn_t1}</div>
                    <div class="vs-label">vs</div>
                    <div class="team-name">{t2_str}{warn_t2}</div>
                </div>"""

            sitout_html = ""
            if sit_names:
                sitout_html = f'<div class="sitout-bar">💺 Sitting out: {", ".join(sit_names)}</div>'

            st.markdown(f"""
            <div class="game-block">
                <div class="game-header">GAME {r.round_num}</div>
                <div class="courts-grid">{cells_html}</div>
                {sitout_html}
            </div>
            """, unsafe_allow_html=True)

            # ── Score entry for this round ────────
            if st.session_state.get("session_code"):
                with st.expander(f"📝 Enter scores for Game {r.round_num}"):
                    existing_scores = load_scores(st.session_state.session_code)
                    for court in r.courts:
                        t1_name = " & ".join(p.name for p in court.team1.players)
                        t2_name = " & ".join(p.name for p in court.team2.players)
                        existing = existing_scores.get((r.round_num, court.court_num), {})

                        sc1, sc2, sc3 = st.columns([2, 1, 1])
                        sc1.markdown(f"**Court {court.court_num}:** {t1_name} vs {t2_name}")

                        existing_t1 = str(existing["team1_score"]) if "team1_score" in existing else ""
                        existing_t2 = str(existing["team2_score"]) if "team2_score" in existing else ""

                        save_key = f"save_score_{r.round_num}_{court.court_num}"

                        # Clear "saved" banner if the user starts editing again
                        current_t1_val = st.session_state.get(f"score1_{r.round_num}_{court.court_num}", existing_t1)
                        current_t2_val = st.session_state.get(f"score2_{r.round_num}_{court.court_num}", existing_t2)
                        if current_t1_val != existing_t1 or current_t2_val != existing_t2:
                            st.session_state[f"score_saved_{save_key}"] = False

                        score1_raw = sc2.text_input(
                            f"{t1_name} score", placeholder="0",
                            value=existing_t1,
                            key=f"score1_{r.round_num}_{court.court_num}",
                            label_visibility="collapsed",
                        )
                        score2_raw = sc3.text_input(
                            f"{t2_name} score", placeholder="0",
                            value=existing_t2,
                            key=f"score2_{r.round_num}_{court.court_num}",
                            label_visibility="collapsed",
                        )

                        if st.button(f"💾 Save", key=save_key):
                            if not score1_raw.strip() or not score2_raw.strip():
                                st.error("Please enter both scores.")
                            else:
                                try:
                                    score1 = int(score1_raw.strip())
                                    score2 = int(score2_raw.strip())
                                except ValueError:
                                    st.error("Scores must be whole numbers.")
                                else:
                                    from utils import validate_score
                                    error = validate_score(score1, score2, config.scoring_config)
                                    if error:
                                        st.error(error)
                                    else:
                                        save_score(st.session_state.session_code, r.round_num, court.court_num, score1, score2)
                                        st.session_state[f"score_saved_{save_key}"] = True
                                        st.rerun()

                        if st.session_state.get(f"score_saved_{save_key}"):
                            st.success("✅ Score saved!")

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

# ═══════════════════════════════════════════
#  Tab 4 -- The Editor
# ═══════════════════════════════════════════
with tab4:
    if st.session_state.schedule is None:
        st.info("Generate a schedule first.")
    else:
        rounds  = st.session_state.schedule
        players = session_players()
        player_lookup = {p.name: p for p in players}

        st.subheader("✏️ Edit Schedule")
        st.caption("Swap two players, or fully reassign a round. Stats update automatically after any change.")

        round_options = [r.round_num for r in rounds]
        edit_round_num = st.selectbox("Select round to edit", round_options, key="edit_round_select")
        edit_round = next(r for r in rounds if r.round_num == edit_round_num)

        edit_mode = st.radio("Edit mode", ["🔁 Quick Swap", "🛠️ Full Manual Edit"], horizontal=True)

        # ═══════════════════════════════
        # QUICK SWAP MODE
        # ═══════════════════════════════
        if edit_mode == "🔁 Quick Swap":
            st.markdown("**Swap two players' positions within this round** (including swapping with a sit-out).")

            # Build a flat list of (label, court_num, team_num, slot_num) for everyone in this round
            slot_options = []
            for court in edit_round.courts:
                for team_num, team in enumerate([court.team1, court.team2], start=1):
                    for slot_num, p in enumerate(team.players, start=1):
                        label = f"{p.name} (Court {court.court_num}, Team {team_num})"
                        slot_options.append((label, court.court_num, team_num, slot_num, p.name))
            for p in edit_round.sit_outs:
                label = f"{p.name} (Sitting Out)"
                slot_options.append((label, None, None, None, p.name))

            col_a, col_b = st.columns(2)
            choice_a = col_a.selectbox("Player A", [s[0] for s in slot_options], key="swap_a")
            choice_b = col_b.selectbox("Player B", [s[0] for s in slot_options], key="swap_b")

            if st.button("🔁 Swap Players", type="primary"):
                if choice_a == choice_b:
                    st.warning("Select two different players to swap.")
                else:
                    a_data = next(s for s in slot_options if s[0] == choice_a)
                    b_data = next(s for s in slot_options if s[0] == choice_b)
                    _, a_court, a_team, a_slot, a_name = a_data
                    _, b_court, b_team, b_slot, b_name = b_data

                    def get_player_obj(court_num, team_num, slot_num, sit_name):
                        if court_num is None:
                            return None  # sit-out case handled separately
                        court = next(c for c in edit_round.courts if c.court_num == court_num)
                        team  = court.team1 if team_num == 1 else court.team2
                        return team.players[slot_num - 1]

                    def set_player(court_num, team_num, slot_num, new_player):
                        court = next(c for c in edit_round.courts if c.court_num == court_num)
                        team  = court.team1 if team_num == 1 else court.team2
                        team.players[slot_num - 1] = new_player

                    a_player = player_lookup[a_name]
                    b_player = player_lookup[b_name]

                    # Both on courts — straightforward swap
                    if a_court is not None and b_court is not None:
                        set_player(a_court, a_team, a_slot, b_player)
                        set_player(b_court, b_team, b_slot, a_player)

                    # A on court, B sitting out — bring B in, send A to bench
                    elif a_court is not None and b_court is None:
                        set_player(a_court, a_team, a_slot, b_player)
                        edit_round.sit_outs.remove(b_player if b_player in edit_round.sit_outs else
                                                    next(p for p in edit_round.sit_outs if p.name == b_name))
                        edit_round.sit_outs.append(a_player)

                    # B on court, A sitting out — mirror of above
                    elif b_court is not None and a_court is None:
                        set_player(b_court, b_team, b_slot, a_player)
                        edit_round.sit_outs.remove(next(p for p in edit_round.sit_outs if p.name == a_name))
                        edit_round.sit_outs.append(b_player)

                    # Recompute everything
                    config = st.session_state.get("last_config")
                    tracker, sit_summary = recompute_all_stats(rounds, players, config)
                    st.session_state.schedule    = rounds
                    st.session_state.tracker     = tracker
                    st.session_state.sit_summary = sit_summary
                    st.success(f"Swapped {a_name} and {b_name} in Round {edit_round_num}!")
                    st.rerun()

        # ═══════════════════════════════
        # FULL MANUAL EDIT MODE
        # ═══════════════════════════════
        else:
            st.markdown(f"**Reassign every player for Round {edit_round_num}.**")
            st.caption("Every player in your roster appears in each dropdown — make sure no one is selected twice.")

            all_names = [p.name for p in players]
            new_assignments = {}  # (court_num, team_num, slot_num) -> name

            for court in edit_round.courts:
                st.markdown(f"**Court {court.court_num}**")
                c1, c2, c3, c4 = st.columns(4)
                cols = [c1, c2, c3, c4]
                idx = 0
                for team_num, team in enumerate([court.team1, court.team2], start=1):
                    for slot_num, p in enumerate(team.players, start=1):
                        key = f"manual_{edit_round_num}_{court.court_num}_{team_num}_{slot_num}"
                        selected = cols[idx].selectbox(
                            f"T{team_num}P{slot_num}",
                            all_names,
                            index=all_names.index(p.name) if p.name in all_names else 0,
                            key=key,
                            label_visibility="collapsed",
                        )
                        new_assignments[(court.court_num, team_num, slot_num)] = selected
                        idx += 1
                st.divider()

            if st.button("💾 Save Manual Changes", type="primary"):
                chosen_names = list(new_assignments.values())
                if len(chosen_names) != len(set(chosen_names)):
                    st.error("⚠️ The same player is assigned to multiple slots. Please fix before saving.")
                else:
                    for (court_num, team_num, slot_num), name in new_assignments.items():
                        court = next(c for c in edit_round.courts if c.court_num == court_num)
                        team  = court.team1 if team_num == 1 else court.team2
                        team.players[slot_num - 1] = player_lookup[name]

                    # Recompute sit-outs: anyone not assigned to a court slot this round sits out
                    assigned_names = set(chosen_names)
                    edit_round.sit_outs = [p for p in players if p.name not in assigned_names]

                    config = st.session_state.get("last_config")
                    tracker, sit_summary = recompute_all_stats(rounds, players, config)
                    st.session_state.schedule    = rounds
                    st.session_state.tracker     = tracker
                    st.session_state.sit_summary = sit_summary
                    st.success(f"Round {edit_round_num} updated!")
                    st.rerun()

# ─────────────────────────────────────────────
#  Footer
# ─────────────────────────────────────────────
st.markdown("---")
st.caption("Picklemade · Built with Streamlit 🏓")