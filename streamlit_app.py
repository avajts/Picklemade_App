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
tab1, tab2, tab3 = st.tabs(["📅 Schedule", "🏆 Stats", "📊 Pair Matrix"])

# ═══════════════════════════════════════════
#  Tab 1 — Schedule
# ═══════════════════════════════════════════
with tab1:
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
                    pdf.cell(col_width, row_height, f"Court {court.court_num}",
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

            # Build court cells HTML
            cells_html = ""
            for court in r.courts:
                t1 = court.team1
                t2 = court.team2
                t1_str  = " & ".join(p.name for p in t1.players)
                t2_str  = " & ".join(p.name for p in t2.players)
                warn_t1 = '<span class="warn-tag">⚠️ same gender</span>' if not t1.is_mixed else ""
                warn_t2 = '<span class="warn-tag">⚠️ same gender</span>' if not t2.is_mixed else ""

                cells_html += f"""
                <div class="court-cell">
                    <div class="court-title">Court {court.court_num}</div>
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