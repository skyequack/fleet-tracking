from .extensions import db
from .validation import page_args


def paginated(stmt, serialize):
    """Run a SELECT with ?page=&per_page= and return the standard list envelope (ARCHITECTURE.md 12, R7)."""
    page, per_page = page_args()
    result = db.paginate(stmt, page=page, per_page=per_page, error_out=False, count=True)
    return {"items": [serialize(row) for row in result.items], "page": page, "per_page": per_page,
            "total": result.total}
