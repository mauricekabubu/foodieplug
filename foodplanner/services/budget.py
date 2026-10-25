from django.db.models import Sum
from django.utils import timezone

from apps.budgets.models import Budget, BudgetEntry


# =========================================================
# BUDGET SERVICES
# =========================================================


# Create a budget
def create_budget(
    user,
    amount,
    start_date,
    end_date,
    name="Food Budget"
):
    """Create a new budget for a user."""

    if amount <= 0:
        raise ValueError(
            "Budget amount must be greater than zero."
        )

    if end_date < start_date:
        raise ValueError(
            "End date cannot be before start date."
        )

    return Budget.objects.create(
        user=user,
        name=name,
        amount=amount,
        start_date=start_date,
        end_date=end_date
    )


# Get all budgets belonging to a user
def get_user_budgets(user):
    """Return all budgets belonging to a user."""

    return Budget.objects.filter(
        user=user
    )


# Get a specific budget
def get_budget(user, budget_id):
    """Return a specific budget belonging to a user."""

    return Budget.objects.filter(
        id=budget_id,
        user=user
    ).first()


# Get the current active budget
def get_current_budget(user, date=None):
    """Return the budget covering the given date."""

    if date is None:
        date = timezone.localdate()

    return Budget.objects.filter(
        user=user,
        start_date__lte=date,
        end_date__gte=date
    ).order_by("-start_date", "-pk").first()


# Add an expense to a budget
def add_expense(
    budget,
    amount,
    description,
    date,
    food=None
):
    """Add an actual expense to a budget."""

    if amount <= 0:
        raise ValueError(
            "Expense amount must be greater than zero."
        )

    if date < budget.start_date or date > budget.end_date:
        raise ValueError(
            "Expense date must fall within the budget period."
        )

    return BudgetEntry.objects.create(
        budget=budget,
        food=food,
        description=description,
        amount=amount,
        date=date
    )


# Get total amount spent
def get_total_spent(budget):
    """Return the total amount spent from a budget."""

    total = budget.entries.aggregate(
        total=Sum("amount")
    )["total"]

    return total or 0


# Get remaining budget
def get_remaining_budget(budget):
    """Return the amount remaining in the budget."""

    spent = get_total_spent(budget)

    return budget.amount - spent


# Get budget usage percentage
def get_budget_usage(budget):
    """Return the percentage of the budget already spent."""

    spent = get_total_spent(budget)

    if budget.amount == 0:
        return 0

    return (spent / budget.amount) * 100


# Get all expenses for a budget
def get_budget_expenses(budget):
    """Return all expenses belonging to a budget."""

    return budget.entries.select_related(
        "food"
    ).order_by("-date", "-created_at", "-pk")
    
