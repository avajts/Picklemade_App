"""
models.py
---------
This file's job is to define the data structures all other 
files will use.
No logic lives here - only data classes and type defintions.

Data Classes:
Player -> one person (name, gender, coupled partner name)
Team -> 2 players on the same side of a net
CourtAssignment -> 1 court in 1 round (Team vs. Team)
Round -> all courts + sit-outs for a single round
ScheduleConfig -> everything the user inputs, in one clean object
"""

from dataclasses import dataclass, field

# Player 

@dataclass
class Player:
    name: str
    gender: str
    preferred_partners: list[tuple[str, int]] = field(default_factory=list)
    # ^ list of (partner_name, desired_rounds_together)
    avoid_partner: str | None = None
    duper_rating: float | None = None 

    def __post_init__(self):
        self.gender = self.gender.upper()
        assert self.gender in ("M", "F"), f"Gender must be 'M' or 'F', got '{self.gender}'"
        if self.duper_rating is not None:
            assert 0.0 <= self.duper_rating <= 7.0, (
                f"Duper rating must be between 0.0 and 7.0, got {self.duper_rating}"
            )

    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        return isinstance(other, Player) and self.name == other.name
    
    def __repr__(self):
        partners = f", partners={self.preferred_partners}" if self.preferred_partners else ""
        avoid    = f", avoids={self.avoid_partner}" if self.avoid_partner else ""
        rating   = f", rating={self.duper_rating}" if self.duper_rating is not None else ""
        return f"Player({self.name}, {self.gender}{partners}{avoid}{rating})"
    
# Team

@dataclass
class Team:
    players: list[Player]   # always length 2

    def __post_init__(self):
        assert len(self.players) == 2, "A team must have exactly 2 players."

    @property
    def is_mixed(self) -> bool:
        """True if the team has one male and one female player."""
        genders = {p.gender for p in self.players}
        return genders == {"M", "F"}
    
    @property
    def name(self) -> list[str]:
        return [p.name for p in self.players]
    
    def __repr__(self):
        return f"Team({' & '.join(self.name)})"
    

# CourtAssignment (one court, one round)

@dataclass
class CourtAssignment:
    court_num: int
    team1: Team
    team2: Team
    mode: str = "mixed" 

    @property
    def all_players(self) -> list[Player]:
        return self.team1.players + self.team2.players
    
    @property
    def is_fully_mixed(self) -> bool:
        """True if BOTH teams are mixed gender."""
        return self.team1.is_mixed and self.team2.is_mixed
    
    def __repr__(self): 
        return f"Court {self.court_num}: {self.team1} vs {self.team2}"
    
# Round
@dataclass
class Round: 
    round_num: int  # 1-indexed
    courts: list[CourtAssignment] = field(default_factory=list)
    sit_outs: list[Player] = field(default_factory=list)

    @property
    def all_playing(self) -> list[Player]:
        """All players active this round (not sitting out)."""
        return [p for court in self.courts for p in court.all_players]
    
    def __repr__(self): 
        lines = [f"--- Round {self.round_num} ---"]
        for court in self.courts:
            lines.append(f"  {court}")
        if self.sit_outs:
            lines.append(f"  Sit-outs: {[p.name for p in self.sit_outs]}")
        return "\n".join(lines)


# ScheduleConfig (all user inputs in one place)

@dataclass
class ScheduleConfig:
    num_courts: int
    num_rounds: int
    players: list[Player]
    game_mode: str = "mixed"
    court_overrides: dict[tuple[int, int], str] = field(default_factory=dict)

    def get_court_mode(self, round_num: int, court_num: int) -> str:
        return self.court_overrides.get((round_num, court_num), self.game_mode)

    def get_all_preferred_pairs(self) -> dict[tuple[str, str], int]:
        """
        Flattens every player's preferred_partners into a single
        {(name_a, name_b): desired_rounds} dict, deduplicating symmetric entries.
        """
        pairs = {}
        for p in self.players:
            for partner_name, rounds in p.preferred_partners:
                key = tuple(sorted([p.name, partner_name]))
                pairs[key] = rounds   # both directions write the same value, so this is safe
        return pairs

    @property
    def players_per_round(self) -> int:
        return self.num_courts * 4
    
    @property
    def num_sitouts_per_round(self) -> int:
        return max(0, len(self.players) - self.players_per_round)

    @property
    def num_males(self) -> int: 
        return sum(1 for p in self.players if p.gender == "M")
    
    @property
    def num_females(self) -> int:
        return sum(1 for p in self.players if p.gender == "F")

    def __repr__(self):
        return (
            f"ScheduleConfig("
            f"{self.num_courts} courts, "
            f"{self.num_rounds} rounds, "
            f"{len(self.players)} players "
            f"[{self.num_males}M / {self.num_females}F])"
        )