from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from services.pagination import paginate_queryset

from services.budget import (
    create_budget,
    get_user_budgets,
    get_budget,
    get_current_budget,
    add_expense,
    get_budget_usage,        # kept for reference; usage is recomputed below so units are always a percentage
    get_budget_expenses,
)

TEMPLATE = "budgets/budget.html"
logger = logging.getLogger(__name__)

# Status thresholds (percent of budget used)
WATCH_AT = Decimal("70")
RISK_AT = Decimal("85")
OVER_AT = Decimal("100")

MIN_DAYS_FOR_PROJECTION = 7


# =========================================================
# HELPERS
# =========================================================

def _kes(value):
    return f"KES {Decimal(value):,.0f}"


def _plain(value):
    """Decimal -> plain string for an <input> (8000.00 -> '8000')."""
    text = format(Decimal(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse_amount(raw):
    try:
        value = Decimal(str(raw or "").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid amount.")

    if not value.is_finite():
        raise ValueError("Enter a valid amount.")
    if value <= 0:
        raise ValueError("The amount must be greater than zero.")

    if value > Decimal("99999999.99"):
        raise ValueError("The amount must be less than KES 100,000,000.")

    try:
        currency_value = value.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError("Enter a valid amount.")
    if currency_value != value:
        raise ValueError("Enter an amount with no more than two decimal places.")

    return currency_value


def _parse_date(raw, label):
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        raise ValueError(f"Enter a valid {label}.")


def _last_day_of_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _budget_values(budget=None, post=None):
    """Values shown in the budget form: what the user just typed, the saved budget, or sensible defaults."""
    if post is not None:
        return {key: post.get(key, "") for key in ("name", "amount", "start_date", "end_date")}

    if budget is not None:
        return {
            "name": budget.name,
            "amount": _plain(budget.amount),
            "start_date": budget.start_date.isoformat(),
            "end_date": budget.end_date.isoformat(),
        }

    today = date.today()
    return {"name": "", "amount": "", "start_date": today.isoformat(),
            "end_date": _last_day_of_month(today).isoformat()}


def _expense_values(expense=None, post=None):
    if post is not None:
        return {key: post.get(key, "") for key in ("description", "amount", "date")}

    if expense is not None:
        return {"description": expense.description, "amount": _plain(expense.amount),
                "date": expense.date.isoformat()}

    return {"description": "", "amount": "", "date": timezone.localdate().isoformat()}


def _default_expense_date(budget):
    """Use the user's local today when possible, bounded by the budget period."""
    today = timezone.localdate()
    return min(max(today, budget.start_date), budget.end_date).isoformat()


def _budget_metrics(budget):
    """
    Everything the dashboard shows, computed from the user's own budget and expenses.

    Only figures that can be calculated from recorded expenses are produced. Planned spending,
    shopping impact, categories and price comparisons need other modules and are NOT invented here.
    """
    today = date.today()
    expenses = get_budget_expenses(budget)
    totals = expenses.aggregate(total=Sum("amount"), count=Count("id"))
    spent = Decimal(totals["total"] or 0)
    expense_count = totals["count"]
    amount = Decimal(budget.amount)
    remaining = amount - spent
    usage = (spent / amount * 100).quantize(Decimal("0.1")) if amount > 0 else Decimal("0")

    total_days = (budget.end_date - budget.start_date).days + 1
    elapsed = min(max((today - budget.start_date).days + 1, 0), total_days)
    days_left = max((budget.end_date - today).days, 0) if today >= budget.start_date else total_days

    metrics = {
        "total_spent": spent,
        "remaining_budget": remaining,
        "budget_usage": usage,
        "projected_spending": None,
        "avg_weekly_spend": None,
        "spending_over_time": [],
        "budget_alerts": [],
        "budget_insights": [],
    }

    # Projection: straight-line pace, only with at least a week of history
    if elapsed >= MIN_DAYS_FOR_PROJECTION and spent > 0:
        metrics["projected_spending"] = (spent / elapsed * total_days).quantize(Decimal("1"))
        metrics["avg_weekly_spend"] = (spent / elapsed * 7).quantize(Decimal("1"))

    # Weekly buckets from the budget start date
    buckets = {}
    daily_totals = expenses.values("date").annotate(total=Sum("amount")).order_by("date")
    for row in daily_totals:
        index = max((row["date"] - budget.start_date).days, 0) // 7
        buckets[index] = buckets.get(index, Decimal("0")) + Decimal(row["total"])

    for index in sorted(buckets):
        week_start = budget.start_date + timedelta(days=index * 7)
        metrics["spending_over_time"].append({
            "label": week_start.strftime("%d %b"),
            "amount": float(buckets[index]),
        })

    # Alerts (each states the numbers behind it)
    if usage >= OVER_AT:
        metrics["budget_alerts"].append({
            "level": "danger", "title": "Over budget",
            "message": f"You've spent {_kes(spent)} of your {_kes(amount)} budget."})
    elif usage >= RISK_AT:
        metrics["budget_alerts"].append({
            "level": "warning", "title": "At risk",
            "message": f"You've used {usage:.0f}% of your budget. {_kes(remaining)} is left."})
    elif usage >= WATCH_AT:
        metrics["budget_alerts"].append({
            "level": "info", "title": "Keep an eye on spending",
            "message": f"You've used {usage:.0f}% of your budget. {_kes(remaining)} is left."})

    projected = metrics["projected_spending"]
    if projected and projected > amount and usage < OVER_AT:
        metrics["budget_alerts"].append({
            "level": "warning", "title": "Your spending pace may put you over budget",
            "message": f"At this pace you'd spend about {_kes(projected)} by {budget.end_date:%d %b}. "
                       f"This is an estimate from {elapsed} days of recorded expenses."})

    # Insights (plain calculations on recorded expenses)
    if expense_count:
        biggest = expenses.order_by("-amount", "-pk").values("amount", "description").first()
        metrics["budget_insights"].append(
            f"Your biggest recorded expense is {biggest['description']} ({_kes(biggest['amount'])}).")
        metrics["budget_insights"].append(
            f"You've recorded {expense_count} expense{'s' if expense_count != 1 else ''}, "
            f"averaging {_kes(spent / expense_count)} each.")

    if remaining > 0 and days_left > 0:
        metrics["budget_insights"].append(
            f"With {days_left} day{'s' if days_left != 1 else ''} left, that's about "
            f"{_kes(remaining / days_left)} a day to stay within budget (a calculation, not a target).")

    return metrics, expenses


def _filter_budget_expenses(expenses, query):
    query = (query or "").strip()[:100]
    if not query:
        return expenses

    condition = Q(description__icontains=query)
    for date_format in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            condition |= Q(date=datetime.strptime(query, date_format).date())
            break
        except ValueError:
            continue

    try:
        amount = Decimal(query)
        if amount.is_finite():
            condition |= Q(amount=amount)
    except InvalidOperation:
        pass

    return expenses.filter(condition)


# =========================================================
# BUDGET DASHBOARD
# =========================================================

@login_required
def budget_dashboard(request):
    """Display the user's budgets and the current budget summary."""
    active_budget = get_current_budget(request.user, date.today())
    expense_query = request.GET.get("expense_q", "").strip()[:100]
    budgets = get_user_budgets(request.user).order_by("-start_date", "-pk")
    budgets_pagination = paginate_queryset(
        request, budgets, 10, page_param="budgets_page"
    )

    # The template reads `budget` and `expenses` on both the dashboard and the detail page.
    context = {
        "budgets": budgets_pagination["page_obj"].object_list,
        "budgets_page_obj": budgets_pagination["page_obj"],
        "budgets_page_links": budgets_pagination["page_links"],
        "budgets_page_query": budgets_pagination["page_query"],
        "budget": active_budget,
        "current_budget": active_budget,
        "expense_default_date": _default_expense_date(active_budget) if active_budget else "",
        "expenses": [],
    }

    if active_budget:
        metrics, expenses = _budget_metrics(active_budget)
        expenses = _filter_budget_expenses(expenses, expense_query)
        expenses_pagination = paginate_queryset(
            request, expenses, 15, page_param="expenses_page"
        )
        context.update(metrics)
        context["expenses"] = expenses_pagination["page_obj"].object_list
        context["expenses_page_obj"] = expenses_pagination["page_obj"]
        context["expenses_page_links"] = expenses_pagination["page_links"]
        context["expenses_page_query"] = expenses_pagination["page_query"]
        context["expense_count"] = expenses_pagination["page_obj"].paginator.count
        context["expense_query"] = expense_query

    return render(request, TEMPLATE, context)


# =========================================================
# CREATE BUDGET
# =========================================================

@login_required
def create_budget_view(request):
    """Create a new food budget."""
    if request.method == "POST":
        values = _budget_values(post=request.POST)

        try:
            name = values["name"].strip() or "Food Budget"
            amount = _parse_amount(values["amount"])
            start_date = _parse_date(values["start_date"], "start date")
            end_date = _parse_date(values["end_date"], "end date")

            if end_date < start_date:
                raise ValueError("The end date can't be before the start date.")

            budget = create_budget(user=request.user, amount=amount, start_date=start_date,
                                   end_date=end_date, name=name)

            messages.success(request, f"Your “{budget.name}” budget is ready.")
            return redirect("budgets:detail", budget_id=budget.id)

        except ValueError as error:
            messages.error(request, str(error))      # re-render below so nothing typed is lost
    else:
        values = _budget_values()

    return render(request, TEMPLATE, {"values": values})


# =========================================================
# BUDGET DETAIL
# =========================================================

@login_required
def budget_detail(request, budget_id):
    """Display a specific budget and its expenses."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    metrics, expenses = _budget_metrics(budget)
    expense_query = request.GET.get("expense_q", "").strip()[:100]
    expenses = _filter_budget_expenses(expenses, expense_query)
    expenses_pagination = paginate_queryset(request, expenses, 15)

    context = {
        "budget": budget,
        "expense_default_date": _default_expense_date(budget),
        "expenses": expenses_pagination["page_obj"].object_list,
        "page_obj": expenses_pagination["page_obj"],
        "page_links": expenses_pagination["page_links"],
        "page_query": expenses_pagination["page_query"],
        "pagination_label": "Budget expense pages",
        "expense_count": expenses_pagination["page_obj"].paginator.count,
        "expense_query": expense_query,
    }
    context.update(metrics)

    return render(request, TEMPLATE, context)


# =========================================================
# ADD EXPENSE
# =========================================================

@login_required
def add_expense_view(request, budget_id):
    """Add an expense to a specific budget."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    if request.method == "POST":
        values = _expense_values(post=request.POST)

        try:
            description = values["description"].strip()
            if not description:
                raise ValueError("Enter what the expense was for.")
            if len(description) > 200:
                raise ValueError("The description must be 200 characters or fewer.")

            amount = _parse_amount(values["amount"])
            expense_date = _parse_date(values["date"], "date")

            add_expense(budget, amount, description, expense_date)
            messages.success(request, "Expense added to your budget.")
            return redirect("budgets:detail", budget_id=budget.id)

        except ValueError as error:
            messages.error(request, str(error))
    else:
        values = _expense_values()

    return render(request, TEMPLATE, {"budget": budget, "values": values})


# =========================================================
# CURRENT BUDGET
# =========================================================

@login_required
def current_budget(request):
    """Redirect to the user's currently active budget."""
    budget = get_current_budget(request.user, date.today())

    if not budget:
        messages.info(request, "You don't have an active budget yet. Create one to start tracking expenses.")
        return redirect("budgets:dashboard")

    return redirect("budgets:detail", budget_id=budget.id)


# =========================================================
# EDIT BUDGET
# =========================================================

@login_required
def edit_budget_view(request, budget_id):
    """Edit an existing budget."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    if request.method == "POST":
        values = _budget_values(post=request.POST)

        try:
            name = values["name"].strip() or "Food Budget"
            amount = _parse_amount(values["amount"])
            start_date = _parse_date(values["start_date"], "start date")
            end_date = _parse_date(values["end_date"], "end date")

            if end_date < start_date:
                raise ValueError("The end date can't be before the start date.")

            budget.name = name
            budget.amount = amount
            budget.start_date = start_date
            budget.end_date = end_date
            budget.save()

            messages.success(request, f"Your “{budget.name}” budget has been updated.")
            return redirect("budgets:detail", budget_id=budget.id)

        except ValueError as error:
            messages.error(request, str(error))
    else:
        values = _budget_values(budget=budget)

    return render(request, TEMPLATE, {"budget": budget, "values": values})


# =========================================================
# DELETE BUDGET
# =========================================================

@login_required
def delete_budget_view(request, budget_id):
    """Delete a specific budget (confirmation page, then POST)."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    if request.method == "POST":
        name = budget.name
        try:
            budget.delete()
        except Exception:
            logger.exception(
                "Could not delete budget %s for user %s",
                budget_id,
                request.user.pk,
            )
            messages.error(request, "We couldn't delete that budget. Nothing was changed.")
            return redirect("budgets:detail", budget_id=budget_id)

        messages.success(request, f"Your “{name}” budget has been deleted.")
        return redirect("budgets:dashboard")

    return render(request, TEMPLATE, {"budget": budget})


# =========================================================
# EDIT EXPENSE
# =========================================================

@login_required
def edit_expense_view(request, budget_id, expense_id):
    """Edit an expense."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    expense = budget.entries.filter(id=expense_id).first()

    if not expense:
        messages.error(request, "Expense not found.")
        return redirect("budgets:detail", budget_id=budget.id)

    if request.method == "POST":
        values = _expense_values(post=request.POST)

        try:
            description = values["description"].strip()
            if not description:
                raise ValueError("Enter what the expense was for.")

            amount = _parse_amount(values["amount"])
            expense_date = _parse_date(values["date"], "date")

            expense.description = description
            expense.amount = amount
            expense.date = expense_date
            expense.save()

            messages.success(request, "Your expense has been updated.")
            return redirect("budgets:detail", budget_id=budget.id)

        except ValueError as error:
            messages.error(request, str(error))
    else:
        values = _expense_values(expense=expense)

    return render(request, TEMPLATE, {"budget": budget, "expense": expense, "values": values})


# =========================================================
# DELETE EXPENSE
# =========================================================

@login_required
def delete_expense_view(request, budget_id, expense_id):
    """Delete an expense from a budget (confirmation page, then POST)."""
    budget = get_budget(request.user, budget_id)

    if not budget:
        messages.error(request, "That budget couldn't be found.")
        return redirect("budgets:dashboard")

    expense = budget.entries.filter(id=expense_id).first()

    if not expense:
        messages.error(request, "That expense couldn't be found.")
        return redirect("budgets:detail", budget_id=budget.id)

    if request.method == "POST":
        try:
            expense.delete()
        except Exception:
            logger.exception(
                "Could not delete expense %s from budget %s for user %s",
                expense_id,
                budget_id,
                request.user.pk,
            )
            messages.error(request, "We couldn't delete that expense. Nothing was changed.")
            return redirect("budgets:detail", budget_id=budget.id)

        messages.success(request, "Expense removed from your budget.")
        return redirect("budgets:detail", budget_id=budget.id)

    return render(request, TEMPLATE, {"budget": budget, "expense": expense})
