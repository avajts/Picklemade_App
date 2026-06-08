# 🏓 Picklemade — Pickleball Coordinator

A smart mixed doubles scheduler for recreational pickleball groups. Built with Python and Streamlit.

---

## What It Does

Picklemade takes your group of players and automatically builds a fair, balanced schedule across any number of courts and rounds. It handles the hard parts — mixed gender pairing, couple preferences, sit-out rotations, and repeat-play tracking — so you can focus on the game.

**Key features:**
- Mixed doubles scheduling (1M + 1F per team) with same-gender fallback when needed
- Fair sit-out rotation when player count doesn't divide evenly into courts
- Couple support — specify how many rounds a couple plays together as partners
- Repeat-play minimization — players avoid partnering or facing the same person too often
- Partner and opponent history matrices for full transparency
- Warnings when constraints can't be fully satisfied

---

## How to Use It

### 1. Set Up Your Session
In the sidebar, enter:
- **Number of courts** — how many courts are available each round
- **Number of rounds** — how many rounds will be played

### 2. Add Players
For each player enter:
- Their **name**
- Their **gender** (M or F)
- Optionally, their **coupled partner** and how many rounds they want to play together

### 3. Generate the Schedule
Click **Generate Schedule**. The app will:
- Assign sit-outs fairly if needed
- Lock in couple pairings for their requested rounds
- Fill remaining courts using a greedy scoring algorithm that minimizes repeat pairings

### 4. View Results
- **📅 Schedule tab** — round-by-round court assignments with gender icons and same-gender warnings
- **🏆 Stats tab** — sit-out counts, partner repeats, opponent repeats
- **📊 Pair Matrix tab** — full N×N grids showing how often every pair has played together or against each other

---

## Example

| Setting | Value |
|---|---|
| Courts | 3 |
| Rounds | 8 |
| Players | 13 (7F / 6M) |
| Sit-outs per round | 1 (rotates fairly) |
| Couple | Alice & Bob, 5 rounds together |

With these settings, Picklemade guarantees:
- Every team is mixed gender where possible
- Alice and Bob are partnered for exactly 5 of the 8 rounds
- No player sits out more than once
- Partner and opponent repeats are minimized across all rounds

---

## Scheduling Algorithm

Picklemade uses a **greedy scoring approach** with retry logic:

```
For each round:
  1. Select sit-outs (fewest sit-outs so far, couples kept together when possible)
  2. Lock in pre-assigned couple pairings for this round
  3. For each court, score all valid candidate groupings:
       - Gender rule:       -1000 per non-mixed team (priority 1)
       - Partner repeats:   -10 per prior round as partners (priority 2)
       - Opponent repeats:  -5 per prior round as opponents (priority 3)
       - Couple bonus:      +500 if a couple is correctly paired this round
  4. Pick the highest-scoring assignment
  5. Retry up to 20 times with reshuffling if greedy gets stuck
```

If no fully valid solution exists after all retries, the app falls back to best-effort scheduling and displays a warning.

---

## File Structure

```
Picklemade_App/
├── streamlit_app.py   # Streamlit UI — inputs, display, tabs
├── scheduler.py       # Core algorithm — greedy scheduling logic
├── constraints.py     # Scoring function and play history tracking
├── models.py          # Dataclasses — Player, Team, CourtAssignment, Round, ScheduleConfig
├── utils.py           # Sit-out rotation, couple round pre-assignment, validation
├── requirements.txt   # Python dependencies
└── README.md
```

---

## Running Locally

```bash
# Clone the repo
git clone https://github.com/avajts/Picklemade_App.git
cd Picklemade_App

# Install dependencies
pip install streamlit pandas

# Run the app
streamlit run streamlit_app.py
```

---

## Requirements

```
streamlit
pandas
```

Python 3.10+ required (uses `X | Y` union type syntax).

---

## Deployment

Deployed via [Streamlit Community Cloud](https://share.streamlit.io) — free, permanent hosting with auto-redeployment on every `git push` to `main`.

---

## Built With

- [Streamlit](https://streamlit.io) — UI framework
- [Pandas](https://pandas.pydata.org) — data display
- Python standard library — `dataclasses`, `itertools`, `collections`, `random`

---

*Built for a group of pickleball-lovers. Forks and contributions welcome!*