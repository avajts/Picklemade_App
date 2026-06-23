"""
standings.py
------------
Computes individual player standings from a schedule + saved scores.
Kept separate from scheduling logic since this is a distinct concern:
reading completed match results and ranking players.
"""

from dataclasses import dataclass, field


@dataclass
class PlayerStanding:
    name: str
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    points_for: int = 0
    points_against: int = 0
    head_to_head_wins: dict = field(default_factory=dict)   # opponent_name -> wins against them

    @property
    def point_differential(self) -> int:
        return self.points_for - self.points_against

    @property
    def win_pct(self) -> float:
        if self.games_played == 0:
            return 0.0
        return self.wins / self.games_played


def compute_standings(rounds: list, scores: dict) -> list[PlayerStanding]:
    """
    rounds: list of Round objects (the schedule)
    scores: dict of {(round_num, court_num): {"team1_score": int, "team2_score": int}}

    Returns a list of PlayerStanding objects, NOT yet sorted/ranked.
    """
    standings: dict[str, PlayerStanding] = {}

    def get_or_create(name: str) -> PlayerStanding:
        if name not in standings:
            standings[name] = PlayerStanding(name=name)
        return standings[name]

    for r in rounds:
        for court in r.courts:
            key = (r.round_num, court.court_num)
            if key not in scores:
                continue   # no score entered yet for this match

            s1 = scores[key]["team1_score"]
            s2 = scores[key]["team2_score"]

            team1_names = [p.name for p in court.team1.players]
            team2_names = [p.name for p in court.team2.players]

            team1_won = s1 > s2

            for name in team1_names:
                ps = get_or_create(name)
                ps.games_played += 1
                ps.points_for     += s1
                ps.points_against += s2
                if team1_won:
                    ps.wins += 1
                else:
                    ps.losses += 1
                for opp_name in team2_names:
                    ps.head_to_head_wins[opp_name] = ps.head_to_head_wins.get(opp_name, 0) + (1 if team1_won else 0)

            for name in team2_names:
                ps = get_or_create(name)
                ps.games_played += 1
                ps.points_for     += s2
                ps.points_against += s1
                if not team1_won:
                    ps.wins += 1
                else:
                    ps.losses += 1
                for opp_name in team1_names:
                    ps.head_to_head_wins[opp_name] = ps.head_to_head_wins.get(opp_name, 0) + (0 if team1_won else 1)

    return list(standings.values())


def rank_standings(standings: list[PlayerStanding]) -> list[dict]:
    """
    Sorts standings using the full tiebreaker chain:
        1. Wins (desc)
        2. Head-to-head (only if exactly 2 players tied on everything above)
        3. Point differential (desc)
        4. Total points scored (desc)
        5. Fewest points allowed (asc)
        6. Flagged as tied if still unresolved

    Returns a list of dicts ready for display, each with a "rank" and "tied" flag.
    """
    # Group players by identical (wins, losses) first — this is our primary tie group
    groups: dict[tuple, list[PlayerStanding]] = {}
    for ps in standings:
        key = (ps.wins, ps.losses)
        groups.setdefault(key, []).append(ps)

    # Sort groups by wins descending
    sorted_group_keys = sorted(groups.keys(), key=lambda k: -k[0])

    final_order: list[tuple[PlayerStanding, bool]] = []   # (player, is_tied)

    for group_key in sorted_group_keys:
        group = groups[group_key]

        if len(group) == 1:
            final_order.append((group[0], False))
            continue

        if len(group) == 2:
            # Try head-to-head first
            a, b = group
            a_beat_b = a.head_to_head_wins.get(b.name, 0)
            b_beat_a = b.head_to_head_wins.get(a.name, 0)

            if a_beat_b != b_beat_a:
                # Clear head-to-head winner
                winner, loser = (a, b) if a_beat_b > b_beat_a else (b, a)
                final_order.append((winner, False))
                final_order.append((loser, False))
                continue
            # else fall through to point differential below

        # 3+ tied, or 2-way tie unresolved by head-to-head — use point differential
        sorted_group = sorted(
            group,
            key=lambda ps: (-ps.point_differential, -ps.points_for, ps.points_against)
        )

        # Check if point differential/points_for/points_against ALSO tie completely
        # (meaning we genuinely can't resolve it)
        all_still_tied = all(
            (ps.point_differential, ps.points_for, ps.points_against) ==
            (sorted_group[0].point_differential, sorted_group[0].points_for, sorted_group[0].points_against)
            for ps in sorted_group
        )

        for ps in sorted_group:
            final_order.append((ps, all_still_tied))

    # Build final ranked output with rank numbers
    result = []
    current_rank = 1
    for i, (ps, is_tied) in enumerate(final_order):
        result.append({
            "rank": current_rank,
            "name": ps.name,
            "wins": ps.wins,
            "losses": ps.losses,
            "games_played": ps.games_played,
            "points_for": ps.points_for,
            "points_against": ps.points_against,
            "point_differential": ps.point_differential,
            "tied": is_tied,
        })
        current_rank += 1

    return result