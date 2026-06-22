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
    Respects per-court gender overrides and skill-balances the
    leftover mixed court when applicable.
    """
    remaining = available[:]
    courts    = []

    # ── Step A: lock in couple pre-assignments ────────────────────────────────
    locked_pairs, remaining = _extract_couple_pairs(remaining, round_num, couple_scheduler)
    court_slots = _pair_up_locked(locked_pairs)
    while len(court_slots) < config.num_courts:
        court_slots.append([])

    # ── Step B: identify leftover mixed court (if any) ───────────────────────
    leftover_court_num = _identify_leftover_mixed_court(config, round_num)

    # Check if a couple already claimed that court — if so, skip skill balance
    leftover_claimed_by_couple = False
    if leftover_court_num is not None:
        slot_index = leftover_court_num - 1
        if slot_index < len(court_slots) and len(court_slots[slot_index]) > 0:
            leftover_claimed_by_couple = True

    skill_balance_applied = False

    # ── Step C: fill each court ────────────────────────────────────────────────
    for i, slot in enumerate(court_slots):
        court_num  = i + 1
        court_mode = config.get_court_mode(round_num, court_num)

        # Try skill-balancing the leftover court first (if eligible)
        if (
            court_num == leftover_court_num
            and not leftover_claimed_by_couple
            and not skill_balance_applied
        ):
            balanced_court = _apply_skill_balance(court_num, remaining, round_num, tracker)
            if balanced_court is not None:
                courts.append(balanced_court)
                used_players = balanced_court.all_players
                remaining = [p for p in remaining if p not in used_players]
                skill_balance_applied = True
                continue   # move to next court

        # Normal fill path
        if len(remaining) < (4 - len(slot) * 2):
            return None

        court, remaining = _fill_court(
            slot, remaining, court_num, round_num, tracker, court_mode
        )
        if court is None:
            return None

        courts.append(court)

    return courts


# ─────────────────────────────────────────────
#  Court filler  (scores all candidates)
# ─────────────────────────────────────────────

def _fill_court(
    slot:         list,
    remaining:    list[Player],
    court_num:    int,
    round_num:    int,
    tracker:      ConstraintTracker,
    court_mode:   str = "mixed",      # ← new parameter
) -> tuple[CourtAssignment | None, list[Player]]:

    if len(slot) == 2:
        court = CourtAssignment(
            court_num=court_num,
            team1=Team(slot[0]),
            team2=Team(slot[1]),
            mode=court_mode, 
        )
        return court, remaining

    if len(slot) == 1:
        candidates = _score_with_locked_team(slot[0], remaining, court_num, round_num, tracker, court_mode)
    else:
        candidates = _score_open_court(remaining, court_num, round_num, tracker, court_mode)

    if not candidates:
        return None, remaining

    best_score = candidates[0][0]
    top = [c for c in candidates if c[0] == best_score]
    chosen_score, chosen_court, chosen_players = random.choice(top)

    updated_remaining = [p for p in remaining if p not in chosen_players]
    return chosen_court, updated_remaining

def _score_open_court(
    pool:        list[Player],
    court_num:   int,
    round_num:   int,
    tracker:     ConstraintTracker,
    court_mode:  str = "mixed",       # ← new parameter
) -> list[tuple[int, CourtAssignment, list[Player]]]:

    scored = []

    if court_mode == "mixed":
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
                        mode=court_mode, 
                    )
                    score = tracker.score_assignment(court, round_num)
                    scored.append((score, court, team1_players + team2_players))

        if not scored:
            scored = _score_fallback_court(pool, court_num, round_num, tracker, court_mode)

    elif court_mode == "womens":
        women_only = [p for p in pool if p.gender == "F"]
        scored = _score_fallback_court(women_only, court_num, round_num, tracker, court_mode)

    elif court_mode == "mens":
        men_only = [p for p in pool if p.gender == "M"]
        scored = _score_fallback_court(men_only, court_num, round_num, tracker, court_mode)

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _score_with_locked_team(
    locked:      list[Player],
    pool:        list[Player],
    court_num:   int,
    round_num:   int,
    tracker:     ConstraintTracker,
    court_mode:  str = "mixed",       # ← new parameter
) -> list[tuple[int, CourtAssignment, list[Player]]]:

    team1  = Team(locked)
    scored = []

    if court_mode == "mixed":
        males   = [p for p in pool if p.gender == "M"]
        females = [p for p in pool if p.gender == "F"]
        for m in males:
            for f in females:
                court = CourtAssignment(court_num=court_num, team1=team1, team2=Team([m, f]), mode=court_mode)
                score = tracker.score_assignment(court, round_num)
                scored.append((score, court, locked + [m, f]))
    elif court_mode == "womens":
        candidates_pool = [p for p in pool if p.gender == "F"]
        for p1, p2 in combinations(candidates_pool, 2):
            court = CourtAssignment(court_num=court_num, team1=team1, team2=Team([p1, p2]), mode=court_mode)
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, locked + [p1, p2]))
    elif court_mode == "mens":
        candidates_pool = [p for p in pool if p.gender == "M"]
        for p1, p2 in combinations(candidates_pool, 2):
            court = CourtAssignment(court_num=court_num, team1=team1, team2=Team([p1, p2]), mode=court_mode)
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, locked + [p1, p2]))

    if not scored:
        for p1, p2 in combinations(pool, 2):
            court = CourtAssignment(court_num=court_num, team1=team1, team2=Team([p1, p2]), mode=court_mode)
            score = tracker.score_assignment(court, round_num)
            scored.append((score, court, locked + [p1, p2]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _score_fallback_court(
    pool:        list[Player],
    court_num:   int,
    round_num:   int,
    tracker:     ConstraintTracker,
    court_mode:  str = "mixed",      # ← add this parameter
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
                mode=court_mode,
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
    Find all preferred-partner pairs whose pre-assigned round matches round_num.
    Each player can have multiple preferred partners, but only ONE will be
    active for any given round (rounds are pre-assigned to be non-overlapping).

    Returns:
        locked_pairs : list of [Player, Player] pairs
        remaining    : available minus all locked players
    """
    locked_pairs: list[list[Player]] = []
    locked_names: set[str]           = set()
    avail_names                      = {p.name for p in available}
    avail_lookup                     = {p.name: p for p in available}

    for p in available:
        if p.name in locked_names:
            continue
        for partner_name, _ in p.preferred_partners:
            if (
                partner_name in avail_names
                and partner_name not in locked_names
                and couple_scheduler.is_couple_round(p.name, partner_name, round_num)
            ):
                partner = avail_lookup[partner_name]
                locked_pairs.append([p, partner])
                locked_names.update([p.name, partner_name])
                break   # this player is locked for this round — stop checking their other partners

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
            mode=court_mode, 
        ))

    return courts

def _identify_leftover_mixed_court(
    config:     ScheduleConfig,
    round_num:  int,
) -> int | None:
    """
    Returns the court_num of the single leftover mixed court for this round,
    if exactly one mixed court exists alongside forced single-gender courts.
    Returns None if no such leftover court applies (e.g. all courts are mixed,
    or multiple mixed courts exist).
    """
    modes = [
        (c, config.get_court_mode(round_num, c))
        for c in range(1, config.num_courts + 1)
    ]
    mixed_courts  = [c for c, m in modes if m == "mixed"]
    forced_courts = [c for c, m in modes if m != "mixed"]

    if len(mixed_courts) == 1 and len(forced_courts) >= 1:
        return mixed_courts[0]
    return None


def _apply_skill_balance(
    leftover_court_num: int,
    remaining:           list[Player],
    round_num:           int,
    tracker:             ConstraintTracker,
) -> CourtAssignment | None:
    """
    Builds the leftover mixed court by pairing the highest-rated available
    woman with the lowest-rated available man, then filling team2 from the
    best-fitting remaining players.

    Returns None if there isn't enough rating data to skill-balance
    (caller should fall back to normal scoring in that case).
    """
    women = [p for p in remaining if p.gender == "F" and p.duper_rating is not None]
    men   = [p for p in remaining if p.gender == "M" and p.duper_rating is not None]

    if len(women) < 1 or len(men) < 2:
        return None   # not enough rated players — fall back to normal scoring

    # Highest-rated woman
    top_woman = max(women, key=lambda p: p.duper_rating)

    # Two lowest-rated men
    sorted_men = sorted(men, key=lambda p: p.duper_rating)
    if len(sorted_men) < 2:
        return None
    low_man_1, low_man_2 = sorted_men[0], sorted_men[1]

    team1_players = [top_woman, low_man_1]

    # Team 2: best remaining woman (if any) + the second low-rated man
    remaining_women = [p for p in women if p.name != top_woman.name]
    if remaining_women:
        team2_partner = max(remaining_women, key=lambda p: p.duper_rating)
    else:
        # No second woman available among rated players — pull any available woman
        any_women = [p for p in remaining if p.gender == "F" and p.name != top_woman.name]
        team2_partner = any_women[0] if any_women else None

    if team2_partner is None:
        return None   # not enough women to fill both teams — fall back

    team2_players = [low_man_2, team2_partner]

    court = CourtAssignment(
        court_num=leftover_court_num,
        team1=Team(team1_players),
        team2=Team(team2_players),
        mode="mixed",
    )
    return court

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