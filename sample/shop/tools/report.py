"""Nightly sales report."""
from billing.totals import daily_total


def build_report(orders):
    """Summarise one day of orders."""
    return {"total": daily_total(orders), "count": len(orders)}


if __name__ == "__main__":
    print(build_report([]))
