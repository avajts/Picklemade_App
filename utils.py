"""
utils.py
--------
This file has two goals:
1: across all rounds, every player who needs to sit out does so as equally as possible
2: given that couple (A, B) wants to play together for k rounds, randomly pick which k 
    of the total rounds those will be
"""

import random
from collections import defaultdict
from models import Player, ScheduleConfig

# Sit-Out Rotation

class SitOutRotation:
    """
    Tracks how many times each player has sat out and selects
    who sits out each round as fairly as possible.
 
    Priority order when choosing who sits out:
        1. Fewest sit-outs so far  (hard sort)
        2. Avoid splitting a couple — if one partner must sit,
           prefer sitting the other too (when numbers allow)
        3. Random tiebreak
    """

    def __init__(self, config: ScheduleConfig):
        self.config = config
        self.sit_out_count: dict[str, int] = {p.name: 0 for p in config.players}

        # Build a fast lookup: player_name -> list of all their preferred partner names
        self.couple_map: dict[str, list[str]] = {
            p.name: [name for name, _ in p.preferred_partners] for p in config.players
        }

    # ---- Public API ------


    def select_sitouts(self, round_num: int) -> list[Player]:
        """
        Return the list of players who will sit out this round.
        Updates internal sit_out_count immediately.
 
        Returns [] if no sit-outs are needed.
        """
        n = self.config.num_sitouts_per_round
        if n == 0:
            return []
        
        chosen = self._pick_sitouts(n)

        for p in chosen:
            self.sit_out_count[p.name] += 1

        return chosen

    def summary(self) -> dict[str, int]:
        """Return a copy of the current sit-out counts (useful for UI display)."""
        return dict(self.sit_out_count)
 
    # ── Internal helpers ─────────────────────
 
    def _pick_sitouts(self, n: int) -> list[Player]:
        """
        Core selection logic. Returns exactly n Player objects.
 
        Steps:
            1. Sort all players by sit_out_count (ascending), random shuffle
               within ties so there's no alphabetical bias.
            2. Try to avoid splitting couples — if one partner is in the
               top-n candidates, pull in their partner too (if possible).
            3. Finalize the list of n players.
        """
        players = self.config.players[:]
        random.shuffle(players)                          # shuffle first for tie fairness
        players.sort(key=lambda p: self.sit_out_count[p.name])
 
        chosen: list[Player] = []
        chosen_names: set[str] = set()
        remaining = [p for p in players]
 
        # Pass 1 — try to respect couple cohesion
        for p in players:
            if len(chosen) == n:
                break
            if p.name in chosen_names:
                continue
 
            partner_names = self.couple_map.get(p.name, [])

            # If this player has any preferred partner also in the candidate pool,
            # and we still have room for both — sit them out together.
            matched = False
            for partner_name in partner_names:
                if partner_name not in chosen_names:
                    partner = self._get_player(partner_name)
                    if partner and len(chosen) + 2 <= n:
                        chosen.append(p)
                        chosen.append(partner)
                        chosen_names.update([p.name, partner_name])
                        matched = True
                        break

            if matched:
                continue
 
            # Otherwise just add this player individually
            chosen.append(p)
            chosen_names.add(p.name)
 
        # Pass 2 — if we still need more sit-outs (couldn't fill via couple logic),
        # fill greedily from the sorted remainder
        if len(chosen) < n:
            for p in players:
                if len(chosen) == n:
                    break
                if p.name not in chosen_names:
                    chosen.append(p)
                    chosen_names.add(p.name)
 
        return chosen[:n]
 
    def _get_player(self, name: str) -> Player | None:
        """Look up a Player object by name."""
        for p in self.config.players:
            if p.name == name:
                return p
        return None
 
 
# ─────────────────────────────────────────────
#  Couple Round Pre-Assignment
# ─────────────────────────────────────────────
 
class CoupleScheduler:
    """
    Decides WHICH rounds each preferred-partner pair plays together,
    ensuring no player is double-booked across multiple partners in the same round.
    """

    def __init__(self, config: ScheduleConfig):
        self.config = config
        self._assignments: dict[frozenset, set[int]] = {}

    def assign_rounds(self) -> None:
        """
        For every player with multiple preferred partners, assign each
        partnership a set of non-overlapping round numbers.
        """
        self._assignments.clear()
        all_pairs = self.config.get_all_preferred_pairs()   # {(a,b): desired_rounds}

        # Track which rounds are already claimed, per player
        claimed_rounds: dict[str, set[int]] = defaultdict(set)

        # Process pairs in a stable but randomized order
        pair_items = list(all_pairs.items())
        random.shuffle(pair_items)

        for (name_a, name_b), desired in pair_items:
            available_for_both = [
                r for r in range(1, self.config.num_rounds + 1)
                if r not in claimed_rounds[name_a] and r not in claimed_rounds[name_b]
            ]
            actual = min(desired, len(available_for_both))
            chosen = set(random.sample(available_for_both, actual)) if actual > 0 else set()

            key = frozenset({name_a, name_b})
            self._assignments[key] = chosen

            for r in chosen:
                claimed_rounds[name_a].add(r)
                claimed_rounds[name_b].add(r)

    def get_couple_rounds(self, name_a: str, name_b: str) -> set[int]:
        key = frozenset({name_a, name_b})
        return self._assignments.get(key, set())

    def is_couple_round(self, name_a: str, name_b: str, round_num: int) -> bool:
        return round_num in self.get_couple_rounds(name_a, name_b)

    def get_all_assignments(self) -> dict[tuple[str, str], set[int]]:
        result = {}
        for key, rounds in self._assignments.items():
            names = tuple(sorted(key))
            result[names] = set(rounds)
        return result

    def summary_table(self) -> list[dict]:
        rows = []
        all_pairs = self.config.get_all_preferred_pairs()
        for (name_a, name_b), desired in all_pairs.items():
            key = frozenset({name_a, name_b})
            assigned = self._assignments.get(key, set())
            rows.append({
                "Pair": f"{name_a} & {name_b}",
                "Desired Rounds Together": desired,
                "Assigned Round #s": ", ".join(str(r) for r in sorted(assigned)),
            })
        return rows
 
# ─────────────────────────────────────────────
#  Validation helpers (called before scheduling)
# ─────────────────────────────────────────────
 
def validate_config(config: ScheduleConfig) -> list[str]:
    """
    Run all pre-schedule validation checks.
    Returns a list of error/warning strings.
    Empty list means config is valid and ready to schedule.
    """
    errors: list[str] = []
    player_names = {p.name for p in config.players}
 
    # ── Basic counts ─────────────────────────
    if config.num_courts < 1:
        errors.append("Must have at least 1 court.")
 
    if config.num_rounds < 1:
        errors.append("Must have at least 1 round.")
 
    if len(config.players) < 4:
        errors.append("Need at least 4 players to fill one court.")
 
    if len(config.players) < config.players_per_round:
        errors.append(
            f"Not enough players: {len(config.players)} players can't fill "
            f"{config.num_courts} courts ({config.players_per_round} needed). "
            f"Reduce courts or add players."
        )
 
    # ── Gender balance warning ────────────────
    m, f = config.num_males, config.num_females

    if config.game_mode == "mixed":
        if abs(m - f) > config.num_courts * 2:
            errors.append(
                f"Gender imbalance warning: {m}M / {f}F. "
                f"Some courts will need same-gender teams. "
                f"Ideal ratio is within {config.num_courts * 2} of each other."
            )
    elif config.game_mode == "womens":
        if m > 0:
            errors.append(
                f"Women's game mode selected but {m} male player(s) are in the list. "
                f"Please remove male players or switch to Mixed mode."
            )
    elif config.game_mode == "mens":
        if f > 0:
            errors.append(
                f"Men's game mode selected but {f} female player(s) are in the list. "
                f"Please remove female players or switch to Mixed mode."
            )
 
    # ── Preferred partner validation ──────────
    for p in config.players:
        total_requested = sum(rounds for _, rounds in p.preferred_partners)

        if total_requested > config.num_rounds:
            errors.append(
                f"'{p.name}' requested {total_requested} total rounds across all preferred "
                f"partners, but there are only {config.num_rounds} rounds. Reduce one or more requests."
            )

        seen_partners = set()
        for partner_name, rounds in p.preferred_partners:
            if partner_name == p.name:
                errors.append(f"'{p.name}' cannot be a preferred partner with themselves.")
            if partner_name not in player_names:
                errors.append(f"'{p.name}' has a preferred partner '{partner_name}' who isn't in the player list.")
            if partner_name in seen_partners:
                errors.append(f"'{p.name}' lists '{partner_name}' as a preferred partner more than once.")
            seen_partners.add(partner_name)
            if rounds > config.num_rounds:
                errors.append(
                    f"'{p.name}' wants {rounds} rounds with '{partner_name}', "
                    f"but there are only {config.num_rounds} rounds total."
                )
 
    # ── Avoid partner validation ──────────────
    for p in config.players:
        if p.avoid_partner:
            if p.avoid_partner not in player_names:
                errors.append(
                    f"Avoid error: '{p.name}' wants to avoid '{p.avoid_partner}' "
                    f"but that name isn't in the player list."
                )
            if p.avoid_partner == p.name:
                errors.append(
                    f"Avoid error: '{p.name}' cannot avoid themselves."
                )
            # Check they're not also listed as a preferred partner
            for (na, nb) in config.get_all_preferred_pairs().keys():
                if set([p.name, p.avoid_partner]) == set([na, nb]):
                    errors.append(
                        f"Conflict: '{p.name}' and '{p.avoid_partner}' "
                        f"are listed as both a preferred partner and an avoid pair."
                    )

    # ── Duper rating validation ───────────────
    for p in config.players:
        if p.duper_rating is not None:
            if not (0.0 <= p.duper_rating <= 7.0):
                errors.append(
                    f"Duper rating error: '{p.name}' has rating {p.duper_rating}, "
                    f"must be between 0.0 and 7.0."
                )

    # ── Court override validation ─────────────
    valid_modes = {"mixed", "womens", "mens"}
    for (r_num, c_num), mode in config.court_overrides.items():
        if r_num < 1 or r_num > config.num_rounds:
            errors.append(f"Court override error: round {r_num} doesn't exist (1-{config.num_rounds}).")
        if c_num < 1 or c_num > config.num_courts:
            errors.append(f"Court override error: court {c_num} doesn't exist (1-{config.num_courts}).")
        if mode not in valid_modes:
            errors.append(f"Court override error: '{mode}' is not a valid mode (mixed/womens/mens).")

    return errors

def validate_score(team1_score: int, team2_score: int, scoring_config: "ScoringConfig") -> str | None:
    """
    Validates a final score against the scoring rules.
    Returns an error message string if invalid, or None if valid.
    """
    if team1_score < 0 or team2_score < 0:
        return "Scores cannot be negative."

    if team1_score == team2_score:
        return "Scores can't be tied — there must be a winner."

    winner = max(team1_score, team2_score)
    margin = abs(team1_score - team2_score)

    if scoring_config.game_to == "timed":
        return None  # any non-tied score is valid for timed games

    game_to = int(scoring_config.game_to)
    win_by  = scoring_config.win_by

    if winner < game_to:
        return f"Winning score must be at least {game_to}."

    if margin < win_by:
        return f"Win must be by at least {win_by} point(s). Current margin is {margin}."

    return None
 



if __name__ == "__main__":
    from models import Player, ScheduleConfig

    players = [
        Player("Alice", "F"), Player("Bob", "M"),
        Player("Carol", "F"), Player("Dan", "M"),
        Player("Eve", "F"),   Player("Frank", "M"),
        Player("Grace", "F"), Player("Hank", "M"),
        Player("Ivy", "F"),   Player("Jack", "M"),
        Player("Kim", "F"),   Player("Leo", "M"),
        Player("Mia", "F"),
    ]

    config = ScheduleConfig(
        num_courts=3,
        num_rounds=8,
        players=players,
        couple_rounds={("Alice", "Bob"): 5, ("Carol", "Dan"): 3}
    )

    errors = validate_config(config)
    print("Errors:", errors or "None")

    cs = CoupleScheduler(config)
    cs.assign_rounds()
    print("Couple assignments:", cs.get_all_assignments())

    sr = SitOutRotation(config)
    for r in range(1, 9):
        outs = sr.select_sitouts(r)
        print(f"Round {r} sit-outs: {[p.name for p in outs]}")
    print("Sit-out totals:", sr.summary())