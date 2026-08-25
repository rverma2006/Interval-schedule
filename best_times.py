# NOTE Not working for special days

"""
Bridges the four data sources (store_slots for staff hours, store_slots for
store hours, booked_slots for existing bookings, special_days for holidays)
into the exact inputs DoctorSlotOptimizer expects, and runs top_slots():

  GET /best-times -> best PT/TTK/TTE-based slot(s) for an appointment.
                      PT/TTK/TTE are REQUIRED -- the doctor's own busy time
                      for this patient type, the prep time before they're
                      seen, and the total call-to-exit footprint. This is
                      the right shape for a doctor who sees several
                      different patient types (each with their own
                      prep/process/exit timing), since each request just
                      supplies its own numbers against the same
                      doctor/store/booking data.

WEEKDAY_TO_DAY_NAME_ID is an ASSUMPTION (day_name_id 1=Monday ... 7=Sunday) --
verify this against your actual data before trusting results.

Special days: if the target date matches a row in special_days for this
store, day_name_id is automatically switched to 8 and store_slots rows are
additionally filtered by special_day_id (that row's id), pulling the
holiday-specific hours instead of the regular weekly ones. If no match is
found, the regular weekday rows are used as normal.
"""

from datetime import date as date_type, datetime, time
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from database import get_db
from models import StoreSlot, BookedSlot, SpecialDay
from Intervalscheduling import DoctorSlotOptimizer, _fmt

router = APIRouter()

# ASSUMPTION -- confirm against real data. date.weekday(): 0=Mon ... 6=Sun.
WEEKDAY_TO_DAY_NAME_ID = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7}


def _time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _datetime_to_minutes_since(dt: datetime, reference_date: date_type) -> int:
    """Minutes elapsed since midnight of reference_date -- naturally handles
    a booking that runs past midnight into the next day too, since it's just
    continuous elapsed minutes, not a reset-at-midnight clock reading."""
    reference_midnight = datetime.combine(reference_date, time.min)
    return int((dt - reference_midnight).total_seconds() // 60)


def _build_optimizer_inputs(
    db: Session,
    store_id: int,
    store_staffusers_id: int,
    target_date: date_type,
    day_name_id: Optional[int] = None,
):
    """Pulls doctor_slots, store_slots, and taken_slots for one
    store+staff+date out of the DB, handling the special_days (holiday)
    override along the way."""
    day_name_id = day_name_id or WEEKDAY_TO_DAY_NAME_ID[target_date.weekday()]
    special_day_id = None

    if day_name_id != 8:
        special_day_row = (
            db.query(SpecialDay)
            .filter(and_(
                SpecialDay.store_id == store_id,
                SpecialDay.day == target_date,
            ))
            .first()
        )
        if special_day_row is not None:
            day_name_id = 8
            special_day_id = special_day_row.id

    def _day_filter():
        f = [StoreSlot.day_name_id == day_name_id]
        if day_name_id == 8:
            f.append(StoreSlot.special_day_id == special_day_id)
        return f

    staff_rows = (
        db.query(StoreSlot)
        .filter(and_(
            StoreSlot.store_staffusers_id == store_staffusers_id,
            StoreSlot.store_id == store_id,
            StoreSlot.status == 1,
            *_day_filter(),
        ))
        .all()
    )
    if not staff_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No working hours found for store_staffusers_id={store_staffusers_id} "
                   f"at store_id={store_id} on day_name_id={day_name_id} ({target_date}).",
        )
    doctor_slots = [
        (_time_to_minutes(r.start_time), _time_to_minutes(r.end_time))
        for r in staff_rows
    ]

    store_rows = (
        db.query(StoreSlot)
        .filter(and_(
            or_(StoreSlot.user_id == None, StoreSlot.user_id == 0),  # noqa: E711
            StoreSlot.store_id == store_id,
            StoreSlot.status == 1,
            *_day_filter(),
        ))
        .all()
    )
    if not store_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No store hours found for store_id={store_id} on "
                   f"day_name_id={day_name_id} ({target_date}).",
        )
    store_slots = [
        (_time_to_minutes(r.start_time), _time_to_minutes(r.end_time))
        for r in store_rows
    ]

    booked_rows = (
        db.query(BookedSlot)
        .filter(and_(
            BookedSlot.store_staffusers_id == store_staffusers_id,
            BookedSlot.booking_date == target_date,
            BookedSlot.active_status != 0,
            BookedSlot.booking_child == 0,
        ))
        .all()
    )
    taken_slots = [
        (
            _datetime_to_minutes_since(b.booking_start_time, target_date),
            _datetime_to_minutes_since(b.booking_end_time, target_date),
        )
        for b in booked_rows
    ]

    return doctor_slots, store_slots, taken_slots, day_name_id, special_day_id


def get_best_times(
    db: Session,
    store_id: int,
    store_staffusers_id: int,
    target_date: date_type,
    PT: int,
    TTK: int,
    TTE: int,
    tolerance_A: Tuple[int, int] = (0, 0),
    tolerance_B: Tuple[int, int] = (0, 0),
    consolidate_idle: str = "end",
    n: int = 3,
    period=None,
    common_job_lengths: Optional[List[int]] = None,
    day_name_id: Optional[int] = None,
    store_open: Optional[int] = None,
    store_close: Optional[int] = None,
):
    doctor_slots, store_slots, taken_slots, day_name_id, special_day_id = (
        _build_optimizer_inputs(db, store_id, store_staffusers_id, target_date, day_name_id)
    )

    optimizer = DoctorSlotOptimizer(
        job_length=PT,
        doctor_slots=doctor_slots,
        store_slot=store_slots,
        taken_slots=taken_slots,
        tolerance_A=tolerance_A,
        tolerance_B=tolerance_B,
        consolidate_idle=consolidate_idle,
    )
    try:
        results = optimizer.top_slots(
            PT, TTK, TTE, n=n, period=period, common_job_lengths=common_job_lengths,
            store_open=store_open, store_close=store_close,
        )
    except ValueError as e:
        # e.g. "TTE must be >= TTK + PT" -- surface as a clean 422, not a 500
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "store_id": store_id,
        "store_staffusers_id": store_staffusers_id,
        "date": str(target_date),
        "day_name_id": day_name_id,
        "special_day_id": special_day_id,
        "doctor_slots": [(_fmt(s), _fmt(e)) for s, e in doctor_slots],
        "store_slots": [(_fmt(s), _fmt(e)) for s, e in store_slots],
        "taken_slots": [(_fmt(s), _fmt(e)) for s, e in taken_slots],
        "best_slots": [
            {
                "call_time": _fmt(r.call_time),
                "doctor_start": _fmt(r.doctor_start),
                "doctor_end": _fmt(r.doctor_end),
                "exit_time": _fmt(r.exit_time),
                "uses_tolerance_A": r.uses_tolerance_A,
                "uses_tolerance_B": r.uses_tolerance_B,
            }
            for r in results
        ],
    }


@router.get("/best-times")
def best_times(
    store_id: int,
    store_staffusers_id: int,
    target_date: date_type = Query(..., description="YYYY-MM-DD"),
    PT: int = Query(..., description="Doctor's own busy time for this patient type, minutes"),
    TTK: int = Query(..., description="Time to key person (prep before doctor), minutes"),
    TTE: int = Query(..., description="Total footprint, call->exit, minutes"),
    tolerance_A_x: int = Query(0, description="tolerance_A percent component"),
    tolerance_A_y: int = Query(0, description="tolerance_A flat-minutes component"),
    tolerance_B_before: int = Query(0),
    tolerance_B_after: int = Query(0),
    consolidate_idle: str = Query("end"),
    n: int = Query(3),
    store_open: Optional[int] = Query(None, description="Override, minutes-of-day"),
    store_close: Optional[int] = Query(None, description="Override, minutes-of-day"),
    db: Session = Depends(get_db),
):
    return get_best_times(
        db=db,
        store_id=store_id,
        store_staffusers_id=store_staffusers_id,
        target_date=target_date,
        PT=PT, TTK=TTK, TTE=TTE,
        tolerance_A=(tolerance_A_x, tolerance_A_y),
        tolerance_B=(tolerance_B_before, tolerance_B_after),
        consolidate_idle=consolidate_idle,
        n=n,
        store_open=store_open, store_close=store_close,
    )