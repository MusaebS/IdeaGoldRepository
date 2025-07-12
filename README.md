Idea Gold Scheduler – Points-Based Core Specification (rev 2025-05-xx)
1 Project Goal
Build a Streamlit application that produces a fair, constraint-aware on-call schedule for a single 4-week (≈ 28-day) block.

Fairness axis	Rule (measured in points)
Per-label quotas	Each resident must earn an integer point quota for every shift label, obtained by Hare-Niemeyer rounding of their fractional share.
Weekend burden	Weekend points are balanced so no resident is disproportionately loaded.
Overall balance	The total points across all labels for any two full-participation residents may differ by ≤ 1 point (cross-bucket fairness).

2 Hard Constraints
Shift templates
Fields: label, role (Junior / Senior), night_float flag, thu_is_weekend flag, points value.

Participants
Lists of juniors & seniors; optional night-float eligibility per person.

Absences

Leaves (compensated — quota unchanged).

Rotator periods (un-compensated — quotas scaled by active-days / block-span).

Rest spacing
A resident must have at least min_gap days between any two shifts, regardless of label.

Night-float blocks
NF must be assigned in fixed-length blocks (e.g., 5 nights).

3 Solution Outline
Compute fractional point quotas per resident & label from template points, availability, extra on-call bias, and rotator scaling.

Round quotas to integers using Hare-Niemeyer with random tie-breaks.

Normalise totals so full participants end within ±1 point of each other.

Pre-assign night-float blocks.

Iterative day-by-day fill obeying role, rest spacing, weekend rules, and deficit-first selection.

Weekend balancer swaps shifts to equalise weekend-point totals.

Output schedule table, point summary, fairness report, and download files.

4 Tech Stack
Layer	Choice
Language	Python 3.11
UI	Streamlit ≥ 1.33 (layout="wide")
Data	pandas 2.x (tables & CSV)
RNG	random (global seed)
Deployment	Streamlit Cloud or any Python host; multi-file layout accepted (no single-file restriction).

5 Minimum Viable Modules
Module	Responsibility
data_models.py	Dataclasses / dict schemas for templates, residents, absences.
scheduler.py	Core algorithm (steps 1-6 above).
app.py	Streamlit UI (upload / entry of data, button to run scheduler, display & downloads).

(Exact helper names and internal structure are flexible as long as the rules and outputs above are met.)

In short: generate a 4-week schedule that meets point quotas, weekend balance, overall ±1 point fairness, global min_gap, night-float blocks, and all availability constraints — surfaced through a simple Streamlit interface and CSV exports.
