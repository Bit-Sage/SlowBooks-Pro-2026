# ============================================================================
# Pay applications (#555) — the owner's progress billing math, in one place.
#
# The math is pure (line_computed, application_totals): Decimal, cents
# half-up, the same arithmetic the route, the PDF and the tests read. The
# history helpers (previous work, retainage held/released to date, deposits)
# read earlier APPROVED applications live, so a voided application leaves
# nothing stale behind.
#
# An application bills a period against the schedule of values:
#   completed_to_date = previous + this_period + stored
# where `previous` is the sum of this_period on earlier approved applications
# for the same SOV line, so stored materials carry in `stored` until they are
# installed (then stored drops and this_period takes them up). Cumulative work
# billed to income equals completed_to_date.
# ============================================================================

from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.accounting import _q

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def line_computed(line, retainage_pct) -> dict:
    """Per-line figures for one application line. `line` carries
    scheduled_value, previous, this_period and stored (Decimals or coercible).
    `retainage_pct` is the application's percent (e.g. 10 for 10%)."""
    scheduled = _q(getattr(line, "scheduled_value", 0))
    previous = _q(getattr(line, "previous", 0))
    this_period = _q(getattr(line, "this_period", 0))
    stored = _q(getattr(line, "stored", 0))
    pct = Decimal(str(retainage_pct or 0))
    completed = _q(previous + this_period + stored)
    pct_complete = float(_q(completed / scheduled * _HUNDRED)) if scheduled else 0.0
    return {
        "scheduled_value": scheduled,
        "previous": previous,
        "this_period": this_period,
        "stored": stored,
        "completed_to_date": completed,
        "pct_complete": pct_complete,
        "balance_to_finish": _q(scheduled - completed),
        "retainage": _q(completed * pct / _HUNDRED),
    }


def application_totals(
    lines,
    *,
    retainage_pct,
    previous_retainage,
    previous_certificates,
    release_retainage=0,
    deposit_applied=0,
) -> dict:
    """The application's totals. `lines` is any iterable of objects carrying
    scheduled_value / previous / this_period / stored. The scalars come from
    history: `previous_retainage` is the prior approved application's
    retainage_to_date, `previous_certificates` the sum of prior approved
    applications' current_due."""
    rows = [line_computed(ln, retainage_pct) for ln in lines]
    scheduled_total = _q(sum((r["scheduled_value"] for r in rows), _ZERO))
    previous_total = _q(sum((r["previous"] for r in rows), _ZERO))
    this_period_total = _q(sum((r["this_period"] for r in rows), _ZERO))
    stored_total = _q(sum((r["stored"] for r in rows), _ZERO))
    completed = _q(sum((r["completed_to_date"] for r in rows), _ZERO))
    retainage_to_date = _q(sum((r["retainage"] for r in rows), _ZERO))
    balance_total = _q(sum((r["balance_to_finish"] for r in rows), _ZERO))

    prev_ret = _q(previous_retainage)
    prev_cert = _q(previous_certificates)
    release = _q(release_retainage)
    deposit = _q(deposit_applied)

    retainage_this_period = _q(retainage_to_date - prev_ret)
    total_earned_less_retainage = _q(completed - retainage_to_date)
    current_due = _q(total_earned_less_retainage - prev_cert + release)
    net_due = _q(current_due - deposit)
    return {
        "scheduled_total": scheduled_total,
        "previous_total": previous_total,
        "this_period_total": this_period_total,
        "stored_total": stored_total,
        "completed_to_date": completed,
        "retainage_to_date": retainage_to_date,
        "previous_retainage": prev_ret,
        "retainage_this_period": retainage_this_period,
        "total_earned_less_retainage": total_earned_less_retainage,
        "previous_certificates": prev_cert,
        "current_due": current_due,
        "balance_to_finish_total": balance_total,
        "release_retainage": release,
        "deposit_applied": deposit,
        "net_due": net_due,
    }


# ── History (earlier APPROVED applications, read live) ──────────────────────


def _approved(db: Session, job_id: int, before_no=None):
    """This job's approved applications, oldest first; optionally only those
    numbered below `before_no` (an application's own history excludes it)."""
    from app.models.pay_applications import PayApplication

    q = db.query(PayApplication).filter(
        PayApplication.job_id == job_id,
        PayApplication.status == "approved",
    )
    if before_no is not None:
        q = q.filter(PayApplication.application_no < before_no)
    return q.order_by(PayApplication.application_no).all()


def previous_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → Σ this_period on earlier approved applications (the
    `previous` column)."""
    out: dict = {}
    for app in _approved(db, job_id, before_no):
        for ln in app.lines:
            if ln.sov_line_id is None:
                continue
            out[ln.sov_line_id] = _q(
                out.get(ln.sov_line_id, _ZERO) + _q(ln.this_period)
            )
    return out


def stored_by_sov_line(db: Session, job_id: int, before_no=None) -> dict:
    """SOV line id → the stored value on the most recent earlier approved
    application (for the billing stored-delta)."""
    out: dict = {}
    for app in _approved(db, job_id, before_no):  # oldest first, last wins
        for ln in app.lines:
            if ln.sov_line_id is None:
                continue
            out[ln.sov_line_id] = _q(ln.stored)
    return out


def application_figures(db: Session, app) -> dict:
    """Full totals for one application, with its lines' `previous` filled
    from history and the history scalars resolved. Reads live, so it is
    correct whether the application is draft or approved."""
    prev = previous_by_sov_line(db, app.job_id, app.application_no)
    rows = []
    for ln in app.lines:
        rows.append(
            _Row(
                scheduled_value=ln.scheduled_value,
                previous=prev.get(ln.sov_line_id, _ZERO),
                this_period=ln.this_period,
                stored=ln.stored,
            )
        )
    return application_totals(
        rows,
        retainage_pct=app.retainage_pct,
        previous_retainage=previous_retainage(db, app),
        previous_certificates=previous_certificates(db, app),
        release_retainage=app.release_retainage,
        deposit_applied=app.deposit_applied,
    )


def previous_retainage(db: Session, app) -> Decimal:
    """The prior approved application's retainage_to_date (the latest one
    numbered below this application)."""
    prior = _approved(db, app.job_id, app.application_no)
    if not prior:
        return _ZERO
    return _retainage_to_date(db, prior[-1])


def _retainage_to_date(db: Session, app) -> Decimal:
    prev = previous_by_sov_line(db, app.job_id, app.application_no)
    rows = [
        _Row(
            scheduled_value=ln.scheduled_value,
            previous=prev.get(ln.sov_line_id, _ZERO),
            this_period=ln.this_period,
            stored=ln.stored,
        )
        for ln in app.lines
    ]
    return _q(
        sum((line_computed(r, app.retainage_pct)["retainage"] for r in rows), _ZERO)
    )


def previous_certificates(db: Session, app) -> Decimal:
    """Σ current_due of the approved applications numbered below this one."""
    total = _ZERO
    for prior in _approved(db, app.job_id, app.application_no):
        total += application_figures(db, prior)["current_due"]
    return _q(total)


def retainage_held_to_date(db: Session, job_id: int) -> Decimal:
    """Owner retainage held to date — the retainage_to_date of the latest
    approved application (cumulative)."""
    apps = _approved(db, job_id)
    if not apps:
        return _ZERO
    return _retainage_to_date(db, apps[-1])


def retainage_released_to_date(db: Session, job_id: int) -> Decimal:
    """Σ release_retainage of approved applications."""
    return _q(sum((_q(a.release_retainage) for a in _approved(db, job_id)), _ZERO))


def deposits_figures(db: Session, job_id: int) -> dict:
    """Owner deposits: received (non-void), applied (Σ approved applications'
    deposit_applied) and unapplied."""
    from app.models.job_deposits import JobDeposit

    received = _q(
        sum(
            (
                _q(d.amount)
                for d in db.query(JobDeposit).filter(
                    JobDeposit.job_id == job_id, JobDeposit.voided_at.is_(None)
                )
            ),
            _ZERO,
        )
    )
    applied = _q(sum((_q(a.deposit_applied) for a in _approved(db, job_id)), _ZERO))
    return {
        "received": received,
        "applied": applied,
        "unapplied": _q(received - applied),
    }


def billed_to_date(db: Session, job_id: int) -> Decimal:
    """Σ approved applications' completed_to_date — owner billing to date."""
    return _q(
        sum(
            (
                application_figures(db, a)["completed_to_date"]
                for a in _approved(db, job_id)
            ),
            _ZERO,
        )
    )


def billed_completed_by_sov_line(db: Session, job_id: int) -> dict:
    """SOV line id → completed_to_date billed across approved applications
    (Σ this_period of all approved + the latest approved application's
    stored). What a line cannot be dropped or shrunk below (#555)."""
    prev = previous_by_sov_line(db, job_id)  # Σ this_period, all approved
    stored = stored_by_sov_line(db, job_id)  # latest approved stored
    keys = set(prev) | set(stored)
    return {k: _q(prev.get(k, _ZERO) + stored.get(k, _ZERO)) for k in keys}


def has_any_application(db: Session, job_id: int) -> bool:
    from app.models.pay_applications import PayApplication

    return (
        db.query(PayApplication.id).filter(PayApplication.job_id == job_id).first()
        is not None
    )


class _Row:
    """A lightweight line for the pure math, so history-filled `previous`
    rides beside the stored this_period / stored."""

    __slots__ = ("scheduled_value", "previous", "this_period", "stored")

    def __init__(self, scheduled_value, previous, this_period, stored):
        self.scheduled_value = scheduled_value
        self.previous = previous
        self.this_period = this_period
        self.stored = stored
