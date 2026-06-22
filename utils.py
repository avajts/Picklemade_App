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

        # Build a fast lookup: player_name -> couple_;artner_name (or None)
        self.couple_map: dict[str, str | None] = {
            p.name: p.couple_partner for p in config.players
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
 
            partner_name = self.couple_map.get(p.name)
 
            # If this player has a coupled partner also in the candidate pool,
            # and we still have room for both — sit them out together.
            if partner_name and partner_name not in chosen_names:
                partner = self._get_player(partner_name)
                if partner and len(chosen) + 2 <= n:
                    chosen.append(p)
                    chosen.append(partner)
                    chosen_names.update([p.name, partner_name])
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
    Decides WHICH round numbers a couple plays together as partners.
 
    Usage:
        cs = CoupleScheduler(config)
        cs.assign_rounds()
        rounds = cs.get_couple_rounds("Alice", "Bob")
        # → e.g. {1, 3, 5, 6, 8}
    """
 
    def __init__(self, config: ScheduleConfig):
        self.config = config
        # Internal store: frozenset({nameA, nameB}) -> set of round numbers
        self._assignments: dict[frozenset, set[int]] = {}
 
    # ── Public API ───────────────────────────
 
    def assign_rounds(self) -> None:
        """
        Randomly assign which rounds each couple plays together.
        Call this once before the scheduler starts.
 
        Respects the desired round count from config.couple_rounds,
        capped at num_rounds to prevent impossible requests.
        """
        self._assignments.clear()
 
        all_rounds = list(range(1, self.config.num_rounds + 1))
 
        for (name_a, name_b), desired in self.config.couple_rounds.items():
            key = frozenset({name_a, name_b})
 
            # Cap at total available rounds
            actual = min(desired, self.config.num_rounds)
 
            if actual <= 0:
                self._assignments[key] = set()
                continue
 
            chosen_rounds = set(random.sample(all_rounds, actual))
            self._assignments[key] = chosen_rounds
 
    def get_couple_rounds(self, name_a: str, name_b: str) -> set[int]:
        """
        Return the set of round numbers where (name_a, name_b)
        are pre-assigned as partners.
        Returns empty set if they are not a registered couple.
        """
        key = frozenset({name_a, name_b})
        return self._assignments.get(key, set())
 
    def is_couple_round(self, name_a: str, name_b: str, round_num: int) -> bool:
        """True if this round is a pre-assigned couple round for (name_a, name_b)."""
        return round_num in self.get_couple_rounds(name_a, name_b)
 
    def get_all_assignments(self) -> dict[tuple[str, str], set[int]]:
        """
        Return a readable copy of all assignments.
        Keys are (name_a, name_b) tuples (sorted alphabetically).
        """
        result = {}
        for key, rounds in self._assignments.items():
            names = tuple(sorted(key))
            result[names] = set(rounds)
        return result
 
    def summary_table(self) -> list[dict]:
        """
        Returns a list of dicts suitable for a Streamlit st.dataframe() call.
        e.g. [{"Couple": "Alice & Bob", "Desired Rounds": 5, "Assigned Rounds": "1,3,5,6,8"}]
        """
        rows = []
        for (name_a, name_b), desired in self.config.couple_rounds.items():
            key = frozenset({name_a, name_b})
            assigned = self._assignments.get(key, set())
            rows.append({
                "Couple": f"{name_a} & {name_b}",
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
 
    # ── Couple validation ─────────────────────
    seen_coupled: dict[str, str] = {}   # name -> their partner
 
    for (name_a, name_b), desired_rounds in config.couple_rounds.items():
        # Both names must exist
        if name_a not in player_names:
            errors.append(f"Couple error: '{name_a}' is not in the player list.")
        if name_b not in player_names:
            errors.append(f"Couple error: '{name_b}' is not in the player list.")
        if name_a == name_b:
            errors.append(f"Couple error: a player cannot be coupled with themselves ('{name_a}').")
 
        # A player can only be in one couple
        for name in (name_a, name_b):
            if name in seen_coupled:
                errors.append(
                    f"Couple error: '{name}' appears in more than one couple. "
                    f"Each player can only have one partner."
                )
            else:
                seen_coupled[name] = name_b if name == name_a else name_a
 
        # Desired rounds can't exceed total rounds
        if desired_rounds > config.num_rounds:
            errors.append(
                f"Couple ({name_a} & {name_b}) want {desired_rounds} rounds together, "
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
            # Check they're not also listed as a couple
            for (na, nb) in config.couple_rounds.keys():
                if set([p.name, p.avoid_partner]) == set([na, nb]):
                    errors.append(
                        f"Conflict: '{p.name}' and '{p.avoid_partner}' "
                        f"are listed as both a couple and an avoid pair."
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