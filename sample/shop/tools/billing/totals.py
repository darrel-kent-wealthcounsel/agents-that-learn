def daily_total(orders):
    """Add up order totals in cents."""
    return sum(o["total"] for o in orders)
