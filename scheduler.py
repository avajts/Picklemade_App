"""
scheduler.py
------------
Main scheduling algorithm for the Pickleball Coordinator.

Builds a full schedule round by round using a greedy scoring approach:
    1. Select sit-outs (fair rotation)
    2. Lock in couple pre-assignments for this round
    3. Fill remaining courts by scoring all valid candidate groupings
    4. Update constraint tracker with finalized assignments
"""

import random
from itertools import combinations
from models import Player, Team, CourtAssignment, Round, ScheduleConfig
from utils import SitOutRotation, CoupleScheduler, validate_config
from constraints import ConstraintTracker

MAX_RETRIES = 20   # how many times to reshuffle and retry if greedy gets stuck


# ─────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────

def build_schedule(config: ScheduleConfig) -> tuple[list[Round], list[str]]:
    """
    Build a full schedule for all rounds.

    Returns:
        rounds   : list of Round objects (the full schedule)
        warnings : list of warning strings for the UI to display
                   (e.g. "Round 3: could not avoid repeat pairings")
    """
    # ── Validate first ───────────────────────
    errors = validate_config(config)
    if errors:
        raise ValueError("Invalid config:\n" + "\n".join(errors))

    # ── Initialize helpers ───────────────────
    sit_out_rotation  = SitOutRotation(config)
    couple_scheduler  = CoupleScheduler(config)
    couple_scheduler.assign_rounds()
    tracker           = ConstraintTracker(config, couple_scheduler)

    rounds:   list[Round] = []
    warnings: list[str]   = []

    # ── Round loop ───────────────────────────
    for round_num in range(1, config.num_rounds + 1):

        # Step 1 — sit-outs
        sit_outs   = sit_out_rotation.select_sitouts(round_num)
        sit_names  = {p.name for p in sit_outs}
        available  = [p for p in config.players if p.name not in sit_names]

        # Step 2 — attempt to fill all courts (with retries)
        courts, warning = _fill_round(
            available, round_num, config, tracker, couple_scheduler
        )
        if warning:
            warnings.append(f"Round {round_num}: {warning}")

        # Step 3 — record round and update history
        completed_round = Round(round_num=round_num, courts=courts, sit_outs=sit_outs)
        tracker.update_round(courts, round_num)
        rounds.append(completed_round)

    return rounds, warnings


# ─────────────────────────────────────────────
#  Round filler
# ─────────────────────────────────────────────

def _fill_round(
    available:        list[Player],
    round_num:        int,
    config:           ScheduleConfig,
    tracker:          ConstraintTracker,
    couple_scheduler: CoupleScheduler,
) -> tuple[list[CourtAssignment], str | None]:
    """
    Try to fill all courts for one round.
    Retries up to MAX_RETRIES times with reshuffling if greedy gets stuck.

    Returns:
        courts  : list of CourtAssignment (one per court)
        warning : a warning string if constraints had to be relaxed, else None
    """
    for attempt in range(MAX_RETRIES):
        shuffled = available[:]
        random.shuffle(shuffled)

        courts = _greedy_fill(shuffled, round_num, config, tracker, couple_scheduler)

        if courts is not None:
            return courts, None

    # All retries exhausted — fill with best effort and warn
    courts = _best_effort_fill(available, round_num, config, tracker, couple_scheduler)
    return courts, "Could not fully satisfy all constraints — some pairings may repeat."


# ─────────────────────────────────────────────
#  Greedy fill  (one attempt)
# ─────────────────────────────────────────────

def _greedy_fill(
    available:        list[Player],
    round_num:        int,
    config:           ScheduleConfig,
    tracker:          ConstraintTracker,
    couple_scheduler: CoupleScheduler,
) -> list[CourtAssignment] | None:
    """
    Attempt to fill all courts greedily for one round.
    Returns a list of CourtAssignments, or None if it gets stuck.
    """
    remaining  = available[:]
    courts     = []
    court_num  = 1

    # ── Step A: lock in couple pre-assignments ───────────────────────────────
    locked_pairs, remaining = _extract_couple_pairs(remaining, round_num, couple_scheduler)

    # ── Step B: fill each court ──────────────────────────────────────────────
    # Pair up locked couples first into court slots, then fill with free players
    court_slots = _pair_up_locked(locked_pairs)  # list of partial courts (1 team locked)

    # Pad with empty slots if we have fewer locked pairs than courts
    while len(court_slots) < config.num_courts:
        court_slots.append([])   # empty slot = needs both teams from remaining pool

    for i, slot in enumerate(court_slots):
        if len(remaining) < (4 - len(slot) * 2):
            return None

        court, remaining = _fill_court(
            slot, remaining, i + 1, round_num, tracker
        )
        if court is None:
            return None

        courts.append(court)

    return courts


# ─────────────────────────────────────────────
#  Court filler  (scores all candidates)
# ─────────────────────────────────────────────

def _fill_court(
    slot:         list,            # [], [[p1,p2]], or [[p1,p2],[p3,p4]]
    remaining:    list[Player],
    court_num:    int,
    round_num:    int,
    tracker:      ConstraintTracker,
) -> tuple[CourtAssignment | None, list[Player]]:

    # Case 1 — two couples already fill the whole court
    if len(slot) == 2:
        team1_players = slot[0]    # [Player, Player]
        team2_players = slot[1]    # [Player, Player]
        court = CourtAssignment(
            court_num=court_num,
            team1=Team(team1_players),
            team2=Team(team2_players),
        )
        return court, remaining

    # Case 2 — one couple fills team1, find best team2 from remaining
    if len(slot) == 1:
        locked_team = slot[0]      # [Player, Player]
        candidates = _score_with_locked_team(
            locked_team, remaining, court_num, round_num, tracker
        )

    # Case 3 — no couples, fill both teams from remaining pool
    else:
        candidates = _score_open_court(remaining, court_num, round_num, tracker)

    if not candidates:
        return None, remaining

    best_score = candidates[0][0]
    top = [c for c in candidates if c[0] == best_score]
    chosen_score, chosen_court, chosen_players = random.choice(top)

    updated_remaining = [p for p in remaining if p not in chosen_players]
    return chosen_court, updated_remaining

def _score_open_court(
    pool:      list[Player],
    court_num: int,
    round_num: int,
    tracker:   ConstraintTracker,
) -> list[tuple[int, CourtAssignment, list[Player]]]:

    scored = []
    mode = tracker.config.game_mode

    if mode == "mixed":
        # Try all mixed assignments first
        males   = [p for p in pool if p.gender == "M"]
        females = [p for p in pool if p.gender == "F"]

        for m1, m2 in combinations(males, 2):
            for f1, f2 in combinations(females, 2):
                for team1_players, team2_players in [
                    ([m1, f1], [m2, f2]),
                    ([m1, f2], [m2, f1]),
                ]:
                    court = CourtAssignment(
                        court_num=court_num,
                        team1=Team(team1_players),
                        team2=Team(team2_players),
                    )
                    score = tracker.score_assignment(court, round_num)
                    scored.append((score, court, team1_players + team2_players))

        # Fallback if no mixed option exists
        if not scored:
            scored = _score_fallback_court(pool, court_num, round_num, tracker)

    else:
        # Single gender — score all combinations directly
        scored = _score_fallback_court(pool, court_num, round_num, tracker)

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _score_with_locked_team(
    locked:    list[Player],   # 2 players already forming team1
    pool:      list[Player],
    court_num: int,
    round_num: int,
    tracker:   ConstraintTracker,
) -> list[tuple[int, CourtAssignment, list[Player]]]:
    """
    Score all valid team2 options from pool, given team1 is locked.
    Prefers mixed team2; falls back to same-gender.
    """
    team1   = Team(locked)
    males   = [p for p in pool if p.gender == "M"]
    females = [p for p in pool if p.gender == "F"]
    scored  = []

    for m in males:
        for f in females:
            court = CourtAssignment(
                court_num=court_num,
                team1=team1,
                team2=Team([m, f]),
            )
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, locked + [m, f]))

    if not scored:
        # Fallback — any pair from pool
        for p1, p2 in combinations(pool, 2):
            court = CourtAssignment(
                court_num=court_num,
                team1=team1,
                team2=Team([p1, p2]),
            )
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, locked + [p1, p2]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _score_fallback_court(
    pool:      list[Player],
    court_num: int,
    round_num: int,
    tracker:   ConstraintTracker,
) -> list[tuple[int, CourtAssignment, list[Player]]]:
    """
    Last resort: score all 4-player combinations regardless of gender.
    Gender penalty is applied inside score_assignment().
    """
    scored = []
    for group in combinations(pool, 4):
        for t1, t2 in _split_four(list(group)):
            court = CourtAssignment(
                court_num=court_num,
                team1=Team(list(t1)),
                team2=Team(list(t2)),
            )
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, list(group)))
    return scored


# ─────────────────────────────────────────────
#  Couple pre-assignment helpers
# ─────────────────────────────────────────────

def _extract_couple_pairs(
    available:        list[Player],
    round_num:        int,
    couple_scheduler: CoupleScheduler,
) -> tuple[list[list[Player]], list[Player]]:
    """
    Find all couples whose pre-assigned round matches round_num.
    Remove them from available and return them as locked pairs.

    Returns:
        locked_pairs : list of [Player, Player] pairs
        remaining    : available minus all locked players
    """
    locked_pairs: list[list[Player]] = []
    locked_names: set[str]           = set()
    avail_names                      = {p.name for p in available}

    for p in available:
        if p.couple_partner and p.name not in locked_names:
            partner_name = p.couple_partner
            if (
                partner_name in avail_names
                and partner_name not in locked_names
                and couple_scheduler.is_couple_round(p.name, partner_name, round_num)
            ):
                partner = next(q for q in available if q.name == partner_name)
                locked_pairs.append([p, partner])
                locked_names.update([p.name, partner_name])

    remaining = [p for p in available if p.name not in locked_names]
    return locked_pairs, remaining


def _pair_up_locked(
    locked_pairs: list[list[Player]],
) -> list[list[Player]]:
    """
    Convert locked couple pairs into court slots.
    Each slot is either:
        - 2 players  → team1 is set, team2 needs filling from remaining pool
        - 4 players  → both teams set (two couples share a court)
    """
    slots = []
    pairs = locked_pairs[:]

    while len(pairs) >= 2:
        # Two locked couples → one full court, stored as two separate sublists
        slot = [pairs.pop(0), pairs.pop(0)]   # [[p1,p2], [p3,p4]]
        slots.append(slot)

    if pairs:
        # One leftover couple → team1 only, needs an opponent
        slots.append([pairs.pop(0)])           # [[p1,p2]]

    return slots


def _split_four(
    group: list[Player],
) -> list[tuple[tuple, tuple]]:
    """Return the 3 unique ways to split 4 players into 2 pairs."""
    a, b, c, d = group
    return [
        ((a, b), (c, d)),
        ((a, c), (b, d)),
        ((a, d), (b, c)),
    ]


# ─────────────────────────────────────────────
#  Best-effort fill  (fallback after all retries)
# ─────────────────────────────────────────────

def _best_effort_fill(
    available:        list[Player],
    round_num:        int,
    config:           ScheduleConfig,
    tracker:          ConstraintTracker,
    couple_scheduler: CoupleScheduler,
) -> list[CourtAssignment]:
    """
    Called only when all MAX_RETRIES attempts fail.
    Fills courts as best as possible, ignoring repeat constraints
    (gender rule still applied). Returns whatever it can build.
    """
    remaining = available[:]
    random.shuffle(remaining)
    courts    = []

    for court_num in range(1, config.num_courts + 1):
        if len(remaining) < 4:
            break
        group    = remaining[:4]
        remaining = remaining[4:]

        # Pick the best gender split among the 3 possible splits
        best = max(
            _split_four(group),
            key=lambda split: (
                Team(list(split[0])).is_mixed + Team(list(split[1])).is_mixed
            )
        )
        courts.append(CourtAssignment(
            court_num=court_num,
            team1=Team(list(best[0])),
            team2=Team(list(best[1])),
        ))

    return courts

if __name__ == "__main__":
    from models import Player, ScheduleConfig

    players = [
        Player("Alice", "F", couple_partner="Bob"),
        Player("Bob",   "M", couple_partner="Alice"),
        Player("Carol", "F"), Player("Dan",   "M"),
        Player("Eve",   "F"), Player("Frank", "M"),
        Player("Grace", "F"), Player("Hank",  "M"),
        Player("Ivy",   "F"), Player("Jack",  "M"),
        Player("Kim",   "F"), Player("Leo",   "M"),
        Player("Mia",   "F"),
    ]
    config = ScheduleConfig(
        num_courts=3, num_rounds=8, players=players,
        couple_rounds={("Alice", "Bob"): 5}
    )
    rounds, warnings = build_schedule(config)

    for r in rounds:
        print(r)
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(" -", w)