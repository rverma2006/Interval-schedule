from Intervalscheduling import DoctorSlotOptimizer, _fmt

def t(h, m=0):
    """Helper: convert hour:minute -> minutes-since-midnight (day 0 only).
    Do NOT use this for anything that crosses midnight -- use dt() instead."""
    return h * 60 + m


def dt(day, h, m=0):
    """Helper: convert (day, hour, minute) -> absolute continuous minutes."""
    return day * 1440 + h * 60 + m
    # Use this instead of t() whenever a shift or booking might cross midnight 


# ==============================================================================
# INPUTS - edit these for your scenario
# ==============================================================================

# The doctor's own working hours, in absolute minutes. Can be:
#   - a single (start, end) tuple or a list of (start, end) tuples 
doctor_slots = [
    (t(8, 30), t(18)),
]

# The store's own open hours:
#   - a single (start, end) tuple or a list of (start, end) tuples 
store_slot = (t(8, 30), t(18))

# Appointments ALREADY booked for this doctor, as a plain list of
# (start, end) tuples. 
taken_slots = [
    (t(9), t(9, 30)),
    (t(10), t(11)),
]

# (x_percent, y_minutes): how much a NEW appointment is allowed to overlap
# an EXISTING neighboring appointment.
# The allowed overlap = max(x% of the new appointment's own PT, rounded up
# to the nearest minute, y_minutes flat). 
tolerance_A = (0, 2)

# (before, after): how many minutes EARLIER than the doctor/store's real
# opening, and how many minutes LATER than their real closing, a new
# appointment is allowed to be placed. This is always tried LAST, only if no
# in-hours placement exists at all.
tolerance_B = (0, 0)

# "end"   -> pack each appointment as EARLY as possible within its gap, so
#            leftover free time collects AFTER it (toward the next booking).
# "start" -> pack as LATE as possible, so leftover free time collects
#            BEFORE it instead.
consolidate_idle = "end"

# Restrict results to a particular time-of-day window. Accepts:
#   "all", "early_morning"/"morning"/"afternoon"/"evening"/"night" -> named presets
#   a raw (start, end) minutes-of-day tuple
preferred_period = "all"

# How many candidate appointment slots to return, ranked best-first.
num_slots_to_return = 3

# A list of the OTHER common doctor busy-time (PT) durations this doctor
# sees for other patient types, in minutes. 
common_PT = [40, 60]


# How long the DOCTOR is actually busy with this specific patient type, in minutes. 
PT = 10

# How many minutes BEFORE reaching the doctor a patient needs for check-in/
# prep, in minutes. 
TTK = 5

# The patient's TOTAL footprint, in minutes, from being called all the way
# to fully exiting (TTK + PT + any post-doctor wrap-up time). 
TTE = 20


# VALIDATE
_missing = [name for name, val in [("PT", PT), ("TTK", TTK), ("TTE", TTE)] if val is None]
if _missing:
    raise ValueError(
        f"PT, TTK, and TTE are all required -- missing: {', '.join(_missing)}. "
        f"Set each to an integer number of minutes before running."
    )
if TTE < TTK + PT:
    raise ValueError(f"TTE ({TTE}) must be >= TTK + PT ({TTK + PT}).")


# RUN

optimizer = DoctorSlotOptimizer(
    job_length=PT,
    doctor_slots=doctor_slots,
    store_slot=store_slot,
    taken_slots=taken_slots,
    tolerance_A=tolerance_A,
    tolerance_B=tolerance_B,
    consolidate_idle=consolidate_idle,
)

results = optimizer.top_slots(
    n=num_slots_to_return, period=preferred_period,
    common_job_lengths=common_PT or None,
    PT=PT, TTK=TTK, TTE=TTE,
)

# Print a summary
print("Effective bookable window(s):")
for w_start, w_end in optimizer.windows:
    print(f"  {_fmt(w_start)} - {_fmt(w_end)}")
print(f"Existing appointments: {[(_fmt(s), _fmt(e)) for s, e in taken_slots]}")
print()

# Print the ranked results 
if not results:
    print("No feasible slot found.")
else:
    print(f"Top {len(results)} recommended slot(s):")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r}")