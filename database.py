"""
database.py
------------
Thin wrapper around Supabase for shared schedule storage and score entry.
Keeps all database calls in one place so the rest of the app never
talks to Supabase directly.
"""

import random
import string
import json
from datetime import datetime, timezone
from supabase import create_client, Client
import streamlit as st


# ─────────────────────────────────────────────
#  Client setup
# ─────────────────────────────────────────────

@st.cache_resource
def get_supabase_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


# ─────────────────────────────────────────────
#  Code generation
# ─────────────────────────────────────────────

def generate_session_code() -> str:
    """Generates a short, memorable code like PICKLE-7X3K."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PICKLE-{suffix}"


# ─────────────────────────────────────────────
#  Serialization helpers
# ─────────────────────────────────────────────

def serialize_schedule(rounds: list) -> list[dict]:
    """Converts Round objects into plain JSON-safe dicts for storage."""
    result = []
    for r in rounds:
        round_dict = {
            "round_num": r.round_num,
            "sit_outs": [p.name for p in r.sit_outs],
            "courts": [],
        }
        for c in r.courts:
            round_dict["courts"].append({
                "court_num": c.court_num,
                "mode": c.mode,
                "team1": [p.name for p in c.team1.players],
                "team2": [p.name for p in c.team2.players],
            })
        result.append(round_dict)
    return result


def serialize_config(config) -> dict:
    """Converts ScheduleConfig + player roster into a plain JSON-safe dict."""
    return {
        "num_courts": config.num_courts,
        "num_rounds": config.num_rounds,
        "game_mode": config.game_mode,
        "court_overrides": {f"{r}_{c}": m for (r, c), m in config.court_overrides.items()},
        "players": [
            {
                "name": p.name,
                "gender": p.gender,
                "preferred_partners": p.preferred_partners,
                "avoid_partner": p.avoid_partner,
                "duper_rating": p.duper_rating,
            }
            for p in config.players
        ],
    }


# ─────────────────────────────────────────────
#  Save / Load schedule sessions
# ─────────────────────────────────────────────

def save_session(rounds: list, config) -> str:
    """
    Saves a generated schedule to Supabase under a new code.
    Returns the generated code.
    """
    client = get_supabase_client()
    code = generate_session_code()

    payload = {
        "code": code,
        "schedule_data": serialize_schedule(rounds),
        "config_data": serialize_config(config),
    }

    client.table("sessions").insert(payload).execute()
    return code


def load_session(code: str) -> dict | None:
    """
    Loads a saved session by code.
    Returns {"schedule_data": [...], "config_data": {...}} or None if not found.
    """
    client = get_supabase_client()
    code = code.strip().upper()

    result = client.table("sessions").select("*").eq("code", code).execute()
    if not result.data:
        return None
    return result.data[0]


# ─────────────────────────────────────────────
#  Score entry
# ─────────────────────────────────────────────

def save_score(session_code: str, round_num: int, court_num: int, team1_score: int, team2_score: int) -> None:
    """
    Saves or updates a score for a specific round+court within a session.
    Uses upsert behavior: if a score already exists for this round+court, overwrite it.
    """
    client = get_supabase_client()

    existing = (
        client.table("scores")
        .select("id")
        .eq("session_code", session_code)
        .eq("round_num", round_num)
        .eq("court_num", court_num)
        .execute()
    )

    payload = {
        "session_code": session_code,
        "round_num": round_num,
        "court_num": court_num,
        "team1_score": team1_score,
        "team2_score": team2_score,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if existing.data:
        score_id = existing.data[0]["id"]
        client.table("scores").update(payload).eq("id", score_id).execute()
    else:
        client.table("scores").insert(payload).execute()


def load_scores(session_code: str) -> dict:
    """
    Loads all scores for a session.
    Returns {(round_num, court_num): {"team1_score": int, "team2_score": int}}
    """
    client = get_supabase_client()
    result = (
        client.table("scores")
        .select("*")
        .eq("session_code", session_code)
        .execute()
    )

    scores = {}
    for row in result.data:
        key = (row["round_num"], row["court_num"])
        scores[key] = {
            "team1_score": row["team1_score"],
            "team2_score": row["team2_score"],
        }
    return scores