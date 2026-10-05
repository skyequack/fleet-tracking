from .extensions import db
from .validation import page_args


def paginated(stmt, serialize):
    """Run a SELECT with ?page=&per_page= and return the standard list envelope (ARCHITECTURE.md 12, R7)."""
    page, per_page = page_args()
    result = db.paginate(stmt, page=page, per_page=per_page, error_out=False, count=True)
    return {"items": [serialize(row) for row in result.items], "page": page, "per_page": per_page,
            "total": result.total}


def paginated_rows(stmt, serialize):
    """Like paginated(), for a SELECT of several columns/entities: `serialize` receives the whole row."""
    from sqlalchemy import func, select

    page, per_page = page_args()
    total = db.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.session.execute(stmt.limit(per_page).offset((page - 1) * per_page)).all()
    return {"items": [serialize(r) for r in rows], "page": page, "per_page": per_page, "total": total}
