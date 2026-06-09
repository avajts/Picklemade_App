"""
constraints.py
--------------
This file tracks the history and score candidate court assignments.
The scheduler will call this every time it's considering a pssible grouping of 4 players.
"""

from collections import defaultdict
from models import Player, Team, CourtAssignment, ScheduleConfig
from utils import CoupleScheduler
 
 
#  Scoring weights  
 
WEIGHT_GENDER_VIOLATION  = -1000   # per non-mixed team
WEIGHT_PARTNER_REPEAT    =   -10   # per prior round as partners
WEIGHT_OPPONENT_REPEAT   =    -5   # per prior round as opponents
WEIGHT_COUPLE_BONUS      =   500   # per couple correctly paired on their assigned round
WEIGHT_AVOID_VIOLATION = -2000 
 

#  ConstraintTracker
 
class ConstraintTracker:
    """
    Maintains partner and opponent history across all completed rounds,
    and exposes a scoring function the scheduler uses to evaluate candidates.
 
    Usage:
        tracker = ConstraintTracker(config, couple_scheduler)
        score   = tracker.score_assignment(court, round_num)
        tracker.update(completed_round)
    """
 
    def __init__(self, config: ScheduleConfig, couple_scheduler: CoupleScheduler):
        self.config = config
        self.couple_scheduler = couple_scheduler
 
        # Symmetric count matrices — access as self.partner_count[nameA][nameB]
        self.partner_count:  dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.opponent_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
 
    # ── Public API ───────────────────────────
 
    def score_assignment(self, court: CourtAssignment, round_num: int) -> int:
        """
        Score a candidate CourtAssignment for a given round.
        Higher is better. The scheduler picks the highest-scoring option.
 
        Scoring breakdown:
            Gender rule         →  -1000 per non-mixed team (priority 1)
            Partner freshness   →  -10   per prior partner pairing (priority 2)
            Opponent freshness  →  -5    per prior opponent pairing (priority 3)
            Couple bonus        →  +500  if a couple is correctly paired this round
        """
        score = 0
        t1, t2 = court.team1, court.team2
 
        # ── Priority 1: Gender ────────────────
        if self.config.game_mode == "mixed":
            if not t1.is_mixed:
                score += WEIGHT_GENDER_VIOLATION
            if not t2.is_mixed:
                score += WEIGHT_GENDER_VIOLATION
        elif self.config.game_mode == "womens":
            if any(p.gender == "M" for p in t1.players + t2.players):
                score += WEIGHT_GENDER_VIOLATION
        elif self.config.game_mode == "mens":
            if any(p.gender == "F" for p in t1.players + t2.players):
                score += WEIGHT_GENDER_VIOLATION
 
        # ── Priority 2: Partner freshness ─────
        score += self._partner_score(t1)
        score += self._partner_score(t2)
 
        # ── Priority 3: Opponent freshness ────
        score += self._opponent_score(t1, t2)
 
        # ── Couple bonus ──────────────────────
        score += self._couple_bonus(t1, round_num)
        score += self._couple_bonus(t2, round_num)
 
        # ── Avoid partner constraint ──────────
        score += self._avoid_score(t1)
        score += self._avoid_score(t2)

        return score
 
    def update(self, court: CourtAssignment) -> None:
        """
        Record the pairings from a finalized CourtAssignment.
        Call this for every court after a round is confirmed.
        """
        p1, p2 = court.team1.players
        p3, p4 = court.team2.players
 
        # Partner pairs
        self._increment_partner(p1.name, p2.name)
        self._increment_partner(p3.name, p4.name)
 
        # Opponent pairs (all cross-team combinations)
        for a in court.team1.players:
            for b in court.team2.players:
                self._increment_opponent(a.name, b.name)
 
    def update_round(self, courts: list[CourtAssignment]) -> None:
        """Convenience method — update all courts in a round at once."""
        for court in courts:
            self.update(court)
 
    # ── Diagnostic helpers (useful for UI display) ───
 
    def partner_summary(self) -> list[dict]:
        """
        Returns a list of dicts showing every pair that has played
        together as partners at least once. Ready for st.dataframe().
        """
        rows = []
        seen = set()
        for a, counts in self.partner_count.items():
            for b, count in counts.items():
                key = frozenset({a, b})
                if key not in seen and count > 0:
                    rows.append({"Player A": a, "Player B": b, "Times as Partners": count})
                    seen.add(key)
        return sorted(rows, key=lambda r: -r["Times as Partners"])
 
    def opponent_summary(self) -> list[dict]:
        """
        Returns a list of dicts showing every pair that has faced
        each other as opponents at least once. Ready for st.dataframe().
        """
        rows = []
        seen = set()
        for a, counts in self.opponent_count.items():
            for b, count in counts.items():
                key = frozenset({a, b})
                if key not in seen and count > 0:
                    rows.append({"Player A": a, "Player B": b, "Times as Opponents": count})
                    seen.add(key)
        return sorted(rows, key=lambda r: -r["Times as Opponents"])
 
    def max_partner_repeats(self) -> int:
        """Highest partner repeat count across all pairs. Useful for validation."""
        if not self.partner_count:
            return 0
        return max(
            count
            for counts in self.partner_count.values()
            for count in counts.values()
        )
 
    def max_opponent_repeats(self) -> int:
        """Highest opponent repeat count across all pairs."""
        if not self.opponent_count:
            return 0
        return max(
            count
            for counts in self.opponent_count.values()
            for count in counts.values()
        )
 
    # ── Private helpers ──────────────────────
 
    def _partner_score(self, team: Team) -> int:
        p1, p2 = team.players
        return self.partner_count[p1.name][p2.name] * WEIGHT_PARTNER_REPEAT
 
    def _opponent_score(self, team1: Team, team2: Team) -> int:
        score = 0
        for a in team1.players:
            for b in team2.players:
                score += self.opponent_count[a.name][b.name] * WEIGHT_OPPONENT_REPEAT
        return score
 
    def _couple_bonus(self, team: Team, round_num: int) -> int:
        p1, p2 = team.players
        if self.couple_scheduler.is_couple_round(p1.name, p2.name, round_num):
            return WEIGHT_COUPLE_BONUS
        return 0
 
    def _avoid_score(self, team: Team) -> int:
        p1, p2 = team.players
        if (
            (p1.avoid_partner and p1.avoid_partner == p2.name) or
            (p2.avoid_partner and p2.avoid_partner == p1.name)
        ):
            return WEIGHT_AVOID_VIOLATION
        return 0

    def _increment_partner(self, name_a: str, name_b: str) -> None:
        """Increment partner count symmetrically."""
        self.partner_count[name_a][name_b] += 1
        self.partner_count[name_b][name_a] += 1
 
    def _increment_opponent(self, name_a: str, name_b: str) -> None:
        """Increment opponent count symmetrically."""
        self.opponent_count[name_a][name_b] += 1
        self.opponent_count[name_b][name_a] += 1
 
if __name__ == "__main__":
    from models import Player, Team, CourtAssignment, ScheduleConfig
    from utils import CoupleScheduler

    players = [
        Player("Alice", "F", couple_partner="Bob"),
        Player("Bob",   "M", couple_partner="Alice"),
        Player("Carol", "F"),
        Player("Dan",   "M"),
    ]
    config = ScheduleConfig(
        num_courts=1, num_rounds=3, players=players,
        couple_rounds={("Alice", "Bob"): 2}
    )
    cs = CoupleScheduler(config)
    cs.assign_rounds()
    print("Couple rounds:", cs.get_couple_rounds("Alice", "Bob"))

    tracker = ConstraintTracker(config, cs)

    # Build a candidate court: Alice+Bob vs Carol+Dan
    court = CourtAssignment(
        court_num=1,
        team1=Team([players[0], players[1]]),  # Alice & Bob
        team2=Team([players[2], players[3]]),  # Carol & Dan
    )

    print("Score (round 1):", tracker.score_assignment(court, round_num=1))
    tracker.update(court)

    print("Score (round 2, after 1 repeat):", tracker.score_assignment(court, round_num=2))
    print("Partner summary:", tracker.partner_summary())
    print("Opponent summary:", tracker.opponent_summary())