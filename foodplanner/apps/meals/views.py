"""
apps/meals/views.py

Meal library, meal detail (with ingredients), meal creation, and the weekly
meal-planner timetable.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages  # type: ignore[import-not-found]
from django.contrib.auth.decorators import login_required  # type: ignore[import-not-found]
from django.db import IntegrityError, OperationalError, transaction  # type: ignore[import-not-found]
from django.db.models import Case, ExpressionWrapper, IntegerField, Value, When  # type: ignore[import-not-found]
from django.db.models.functions import Coalesce  # type: ignore[import-not-found]
from django.shortcuts import redirect, render  # type: ignore[import-not-found]
from django.urls import reverse  # type: ignore[import-not-found]
from services.pagination import paginate_queryset
from django.utils import timezone  # type: ignore[import-not-found]
from django.views.decorators.http import require_POST  # type: ignore[import-not-found]
from django.utils.http import url_has_allowed_host_and_scheme  # type: ignore[import-not-found]

from services.meals import (
    get_meal_by_id,
    get_user_available_meal,
    get_user_available_meals,
    search_user_available_meals,
    get_user_archived_meals,
    get_user_favorite_meals,
    get_user_meal_preference,
    is_user_favorite,
    get_meal_ingredients,
    create_meal,
    add_meal_ingredient,
    remove_meal_ingredient,
    get_user_schedule,
    schedule_meal,
    move_scheduled_meal,
    mark_scheduled_meal_cooked,
    update_servings,
    remove_scheduled_meal,
)

from services.timetable import (
    MEAL_TYPES,
    get_week_start,
    get_week_end,
    get_week_dates,
    create_empty_timetable,
    create_weekly_timetable,
    validate_timetable,
)
from apps.image_uploads import validate_image_upload
from apps.storage import CloudinaryStorageError

from services.foods import (
    get_all_foods,
    get_food_by_id,
)

from services.pantry import (
    convert_quantity,
    get_user_pantry,
    normalize_unit,
    unit_measure,
)
from services.shopping import (
    create_shopping_list,
    get_user_shopping_lists,
    add_shopping_item,
)

logger = logging.getLogger(__name__)

SHOPPING_LIST_NAME = "Meal plan shopping"
REPEAT_THRESHOLD = 3          # "Same meal planned N times" insight
MISSING_DAY_THRESHOLD = 3     # "Thursday needs N ingredients" insight

# Units offered in the ingredient form. If the project already defines a shared
# unit list (pantry / shopping / Food.default_unit choices), import it here
# instead so every module stays compatible.
INGREDIENT_UNITS = ["g", "kg", "ml", "l", "piece", "bunch", "packet"]

# =========================================================
# HELPERS
# =========================================================

def _meal_type_label(meal_type):
    return str(meal_type).replace("_", " ").title()


def _ordered_meals(meals, sort):
    if sort == "za":
        return meals.order_by("-name", "-pk")
    if sort == "time":
        return meals.annotate(
            has_no_time=Case(
                When(prep_time__isnull=True, cook_time__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            total_time=ExpressionWrapper(
                Coalesce("prep_time", Value(0)) + Coalesce("cook_time", Value(0)),
                output_field=IntegerField(),
            ),
        ).order_by("has_no_time", "total_time", "name", "pk")
    return meals.order_by("name", "pk")


def _fmt_qty(value):
    """12.50 -> '12.5', 3.00 -> '3'."""
    text = format(Decimal(value).quantize(Decimal("0.01")), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _back_to_week(request):
    """Redirect to the week the user was planning (hidden `week` field)."""
    raw = request.POST.get("week") or request.GET.get("week")

    try:
        week = date.fromisoformat(raw) if raw else None
    except ValueError:
        week = None

    url = reverse("meals:timetable")
    if week:
        url += f"?date={week.isoformat()}"

    return redirect(url)


def _detail_url(meal_id, anchor=True):
    """URL of a meal's detail page, optionally jumping to the Ingredients section."""
    url = reverse("meals:meal_detail", kwargs={"meal_id": meal_id})
    return f"{url}#ingredients" if anchor else url


def _render_meal_detail(request, meal, form_data=None):
    """
    Render the meal detail page with everything the template needs:
    the meal, its ingredients, the Food Library, units and any form values
    to preserve after a failed submission.
    """
    ingredients = get_meal_ingredients(meal.pk)
    if hasattr(ingredients, "select_related"):
        ingredients = ingredients.select_related("food")

    return render(request, "meals/meals.html", {
        "meal": meal,
        "ingredients": ingredients,
        "foods": get_all_foods(),
        "units": INGREDIENT_UNITS,
        "form_data": form_data or {},
        "scroll_to_ingredients": True,
        "is_favorite": is_user_favorite(request.user, meal),
    })


def _pantry_stock(user):
    """{food_id: {unit: total quantity}} for the user's pantry."""
    stock = {}

    for item in get_user_pantry(user):
        if item.expires_at and item.expires_at < timezone.localdate():
            continue
        by_unit = stock.setdefault(item.food_id, {})
        unit = normalize_unit(item.unit)
        by_unit[unit] = by_unit.get(unit, Decimal("0")) + (item.quantity or Decimal("0"))

    return stock


def _estimate_cost(meal, servings):
    """
    Hook for the Budget/PriceEntry integration.

    Return a Decimal (KES) once price data is available. Returning None means
    "no estimate", and the UI then shows no cost instead of a made-up one.
    """
    return None


def _meal_availability(meal, servings, stock):
    """
    Compare a meal's ingredients with the user's pantry.

    * Compatible units -> convert kilograms/grams or litres/millilitres.
    * Other units -> no conversion is assumed, so the ingredient is missing.
    """
    ingredients = get_meal_ingredients(meal)
    if hasattr(ingredients, "select_related"):
        ingredients = ingredients.select_related("food")
    ingredients = list(ingredients)

    servings = Decimal(servings or 1)
    available = 0
    missing = []

    for ingredient in ingredients:
        needed = (ingredient.quantity or Decimal("0")) * servings
        held = stock.get(ingredient.food_id, {})
        ingredient_dimension, _factor = unit_measure(ingredient.unit)
        have = sum(
            convert_quantity(quantity, pantry_unit, ingredient.unit)
            for pantry_unit, quantity in held.items()
            if unit_measure(pantry_unit)[0] == ingredient_dimension
        )
        ok = have >= needed
        short = needed - have

        if ok:
            available += 1
        else:
            qty = short
            missing.append({
                "food_id": ingredient.food_id,
                "name": ingredient.food.name,
                "quantity": qty,
                "unit": ingredient.unit,
                "label": (
                    f"{ingredient.food.name} — {_fmt_qty(needed)} {ingredient.unit} "
                    f"required, {_fmt_qty(have)} {ingredient.unit} available"
                ),
            })

    total = len(ingredients)

    if total == 0:
        state = "unknown"
    elif not missing:
        state = "ready"
    elif available > 0:
        state = "partial"
    else:
        state = "missing"

    return {"state": state, "available": available, "total": total, "missing": missing}


def _annotate_schedule(schedule, stock, cache):
    key = (schedule.meal_id, schedule.servings)

    if key not in cache:
        cache[key] = _meal_availability(schedule.meal, schedule.servings, stock)

    info = cache[key]
    schedule.pantry_state = info["state"]
    if schedule.is_cooked:
        schedule.cooking_status = "cooked"
    elif info["state"] == "ready":
        schedule.cooking_status = "ready"
    elif info["state"] in {"missing", "partial"}:
        schedule.cooking_status = "missing"
    else:
        schedule.cooking_status = "planned"
    schedule.available_count = info["available"]
    schedule.total_count = info["total"]
    schedule.missing = info["missing"]
    schedule.missing_labels = [m["label"] for m in info["missing"]]
    schedule.cost = _estimate_cost(schedule.meal, schedule.servings)


def _build_insights(rows, week_dates, planned, summary):
    """Insights computed only from real schedule / pantry data."""
    insights = []

    if not planned:
        return insights

    ready = summary["pantry_ready"]
    with_data = [s for s in planned if s.pantry_state != "unknown"]

    if with_data:
        if ready:
            insights.append({
                "kind": "good",
                "text": f"{ready} of {len(planned)} planned meals can be prepared with ingredients already in your pantry.",
            })
        else:
            insights.append({
                "kind": "info",
                "text": "None of this week's planned meals can be fully prepared from your pantry yet.",
            })

    # Days that need the most shopping
    missing_by_day = []
    for index, day in enumerate(week_dates):
        count = sum(
            len(row["cells"][index]["schedule"].missing)
            for row in rows
            if row["cells"][index]["schedule"]
        )
        if count >= MISSING_DAY_THRESHOLD:
            missing_by_day.append((count, day))

    for count, day in sorted(missing_by_day, key=lambda item: -item[0])[:2]:
        insights.append({
            "kind": "warn",
            "text": f"{day.strftime('%A')} needs {count} ingredients you don't currently have.",
        })

    # Repetition (informational only, never blocking)
    counts = {}
    for schedule in planned:
        entry = counts.setdefault(schedule.meal_id, [schedule.meal.name, 0])
        entry[1] += 1

    for name, count in counts.values():
        if count >= REPEAT_THRESHOLD:
            insights.append({"kind": "info", "text": f"{name} is planned {count} times this week."})

    empty = len(week_dates) * len(MEAL_TYPES) - len(planned)
    if empty:
        insights.append({
            "kind": "info",
            "text": f"{empty} meal slot{'s' if empty != 1 else ''} still empty this week.",
        })

    if summary["total_cost"] is not None:
        insights.append({"kind": "info", "text": f"Estimated meal cost this week: KES {summary['total_cost']:,.0f}."})

    return insights


# =========================================================
# WEEKLY TIMETABLE
# =========================================================

@login_required
def timetable(request):
    """Display the user's weekly meal timetable from MealSchedule records."""

    requested_date = request.GET.get("date")

    try:
        requested_date = date.fromisoformat(requested_date) if requested_date else date.today()
    except ValueError:
        messages.error(request, "Choose a valid date from the calendar.")
        requested_date = date.today()

    week_start = get_week_start(requested_date)
    week_end = get_week_end(requested_date)
    today = date.today()

    schedules = list(
        get_user_schedule(request.user)
        .filter(date__gte=week_start, date__lte=week_end)
        .select_related("meal")
    )

    timetable_data = create_empty_timetable(week_start)

    for schedule in schedules:
        if schedule.date in timetable_data and schedule.meal_type in MEAL_TYPES:
            timetable_data[schedule.date][schedule.meal_type] = schedule

    validate_timetable(timetable_data)

    week_dates = [week_start + timedelta(days=i) for i in range(7)]
    stock = _pantry_stock(request.user)
    cache = {}

    rows = []
    planned = []

    for meal_type in MEAL_TYPES:
        cells = []
        for day in week_dates:
            schedule = timetable_data.get(day, {}).get(meal_type) or None
            if schedule:
                _annotate_schedule(schedule, stock, cache)
                planned.append(schedule)
            cells.append({"date": day, "schedule": schedule, "is_today": day == today})
        rows.append({"key": meal_type, "label": _meal_type_label(meal_type), "cells": cells})

    known_costs = [s.cost for s in planned if s.cost is not None]

    summary = {
        "days_planned": len({s.date for s in planned}),
        "meals_planned": len(planned),
        "pantry_ready": sum(1 for s in planned if s.pantry_state == "ready"),
        "total_cost": sum(known_costs) if known_costs else None,
    }

    # Meals offered in the Add Meal modal, with availability for one serving
    meal_options = []
    for meal in get_user_available_meals(request.user):
        info = _meal_availability(meal, 1, stock)
        meal_options.append({
            "id": meal.id,
            "name": meal.name,
            "available": info["available"],
            "total": info["total"],
            "missing": [m["name"] for m in info["missing"]],
        })

    return render(request, "meals/meal.html", {
        # existing context keys
        "timetable": timetable_data,
        "week_start": week_start,
        "week_end": week_end,
        "previous_week": week_start - timedelta(days=7),
        "next_week": week_start + timedelta(days=7),
        "meal_types": MEAL_TYPES,
        # added for the new template
        "today": today,
        "is_current_week": week_start <= today <= week_end,
        "week_param": week_start.isoformat(),
        "days": [{"date": d, "is_today": d == today} for d in week_dates],
        "rows": rows,
        "summary": summary,
        "insights": _build_insights(rows, week_dates, planned, summary),
        "meal_options": meal_options,
    })


# =========================================================
# MOVE A SCHEDULED MEAL
# =========================================================

@login_required
def move_meal_schedule(request, schedule_id):
    """Move a scheduled meal to another day / meal type."""

    if request.method != "POST":
        return _back_to_week(request)

    try:
        target_date = date.fromisoformat(request.POST.get("date", ""))
    except (ValueError, TypeError):
        messages.error(request, "Choose a valid date to move this meal.")
        return _back_to_week(request)

    try:
        meal_type = request.POST.get("meal_type")
        schedule = move_scheduled_meal(
            user=request.user,
            schedule_id=schedule_id,
            target_date=target_date,
            target_meal_type=meal_type,
        )
        if schedule is None:
            raise ValueError("Scheduled meal not found.")

        messages.success(
            request,
            f"{schedule.meal.name} moved to {target_date:%A} {_meal_type_label(meal_type).lower()}.",
        )

    except (ValueError, TypeError) as error:
        messages.error(request, str(error))

    return _back_to_week(request)


@login_required
@require_POST
def cook_meal_schedule(request, schedule_id):
    """Consume this schedule's recipe and mark it cooked (POST only)."""
    try:
        schedule = mark_scheduled_meal_cooked(request.user, schedule_id)
        messages.success(
            request,
            f"{schedule.meal.name} marked as cooked; recipe quantities were deducted from your pantry.",
        )
    except ValueError as error:
        messages.error(request, str(error))
    except OperationalError:
        logger.exception(
            "Database error while cooking scheduled meal %s for user %s",
            schedule_id,
            request.user.pk,
        )
        messages.error(
            request,
            "Your pantry could not be updated right now. Please retry cooking this meal.",
        )

    return _back_to_week(request)


# =========================================================
# ADD MISSING INGREDIENTS TO SHOPPING
# =========================================================

@login_required
def add_missing_to_shopping(request, schedule_id):
    """Add the ingredients a scheduled meal is missing to a shopping list."""

    if request.method != "POST":
        return _back_to_week(request)

    schedule = (
        get_user_schedule(request.user)
        .filter(id=schedule_id)
        .select_related("meal")
        .first()
    )

    if schedule is None:
        messages.error(request, "Scheduled meal not found.")
        return _back_to_week(request)

    info = _meal_availability(schedule.meal, schedule.servings, _pantry_stock(request.user))

    if not info["missing"]:
        messages.success(request, "You already have everything for this meal.")
        return _back_to_week(request)

    try:
        target = next(
            (lst for lst in get_user_shopping_lists(request.user) if lst.name == SHOPPING_LIST_NAME),
            None,
        ) or create_shopping_list(request.user, SHOPPING_LIST_NAME)
    except ValueError as error:
        messages.error(request, str(error))
        return _back_to_week(request)

    added = already_listed = 0
    errors = []

    for item in info["missing"]:
        if target.items.filter(
            food_id=item["food_id"],
            unit=item["unit"],
            is_purchased=False,
            is_archived=False,
        ).exists():
            already_listed += 1
            continue

        try:
            add_shopping_item(
                request.user,
                target.id,
                item["food_id"],
                str(item["quantity"].quantize(Decimal("0.01"))),
                item["unit"],
            )
            added += 1
        except ValueError as error:
            errors.append(str(error))

    if added:
        messages.success(
            request,
            f"{added} item{'s' if added != 1 else ''} added to “{target.name}”.",
        )
    if already_listed:
        messages.info(
            request,
            f"{already_listed} item{'s' if already_listed != 1 else ''} already on the list.",
        )
    if errors:
        logger.warning("Could not add meal ingredients to shopping list: %s", errors)
        messages.error(request, "Some ingredients could not be added: " + "; ".join(errors))
    if not added and not already_listed and not errors:
        messages.info(request, "There are no missing ingredients to add.")

    return _back_to_week(request)


# =========================================================
# MEAL LIBRARY
# =========================================================

# =========================================================
# MEAL DETAIL
# =========================================================

@login_required
def meal_detail(request, meal_id):
    """Display a meal with its ingredients and the add-ingredient form."""

    meal = get_meal_by_id(meal_id)

    if not meal:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    return _render_meal_detail(request, meal)


# =========================================================
# MEAL SEARCH
# =========================================================

@login_required
def meal_search(request):
    """Search the active meal library."""

    query = request.GET.get("q", "").strip()

    meals = search_user_available_meals(request.user, query) if query else get_user_available_meals(request.user)

    # Renders the library template (meals.html); the previous meal.html is the timetable page.
    return render(request, "meals/meals.html", {"meals": meals, "query": query})


# =========================================================
# CREATE MEAL
# =========================================================

@login_required
def create_meal_view(request):
    """Create a meal, then send the user to its detail page to add ingredients."""

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        instructions = request.POST.get("instructions", "").strip()
        prep_raw = request.POST.get("prep_time", "").strip()
        cook_raw = request.POST.get("cook_time", "").strip()
        image = request.FILES.get("image")      # uploaded files live in request.FILES

        try:
            if not name:
                raise ValueError("Meal name is required.")

            try:
                prep_time = int(prep_raw) if prep_raw else None
                cook_time = int(cook_raw) if cook_raw else None
            except ValueError:
                raise ValueError("Prep and cook times must be whole numbers of minutes.")

            if (prep_time is not None and prep_time < 0) or (cook_time is not None and cook_time < 0):
                raise ValueError("Prep and cook times cannot be negative.")

            if image:
                image_error = validate_image_upload(image)
                if image_error:
                    raise ValueError(image_error)

            meal = create_meal(
                name=name,
                description=description,
                instructions=instructions,
                prep_time=prep_time,
                cook_time=cook_time,
                image=image,
            )

            messages.success(request, f"Meal “{meal.name}” created. Now add its ingredients.")
            return redirect(_detail_url(meal.id))

        except ValueError as error:
            messages.error(request, str(error))
        except IntegrityError:
            messages.error(request, "A meal with that name already exists.")
        except CloudinaryStorageError as error:
            messages.error(request, str(error))
        except OSError:
            logger.exception("Could not create meal")
            messages.error(request, "We couldn't save the meal. Please try again.")

    # GET, or a failed POST: the template re-fills the fields from request.POST.
    return render(request, "meals/meals.html")


# =========================================================
# ADD INGREDIENT
# =========================================================

@login_required
def add_ingredient(request, meal_id):
    """Add a food ingredient to a meal."""

    meal = get_meal_by_id(meal_id)

    if not meal:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    if request.method != "POST":
        return redirect(_detail_url(meal.id))

    form_data = {
        "food_id": request.POST.get("food_id", ""),
        "quantity": request.POST.get("quantity", ""),
        "unit": request.POST.get("unit", "").strip(),
    }

    try:
        if not form_data["food_id"]:
            raise ValueError("Please choose a food.")

        try:
            food = get_food_by_id(form_data["food_id"])
        except (ValueError, TypeError):
            food = None
        if not food:
            raise ValueError("That food was not found in the Food Library.")

        try:
            quantity = Decimal(form_data["quantity"])
        except (InvalidOperation, TypeError):
            raise ValueError("Enter a valid quantity, for example 2 or 0.5.")
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")

        if not form_data["unit"]:
            raise ValueError("Please choose a unit.")
        if form_data["unit"] not in INGREDIENT_UNITS:
            raise ValueError("Please choose a unit from the list.")

        add_meal_ingredient(meal, food, quantity, form_data["unit"])

        messages.success(request, f"{food.name} added to {meal.name}.")
        return redirect(_detail_url(meal.id))

    except ValueError as error:
        messages.error(request, str(error))
    except IntegrityError:
        logger.exception("Could not add ingredient to meal %s", meal_id)
        messages.error(request, "That ingredient could not be added. Please try again.")
    except OSError:
        logger.exception("Could not add ingredient")
        messages.error(request, "We couldn't add that ingredient. Please try again.")

    # Error path: keep the meal, ingredients, food selector and the user's entries.
    return _render_meal_detail(request, meal, form_data)


# =========================================================
# REMOVE INGREDIENT
# =========================================================

@login_required
def remove_ingredient(request, meal_id):
    """Remove an ingredient from a meal (POST only)."""

    meal = get_meal_by_id(meal_id)

    if not meal:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    if request.method != "POST":
        return redirect(_detail_url(meal.id))

    unit = request.POST.get("unit", "").strip()

    try:
        try:
            food = get_food_by_id(request.POST.get("food_id"))
        except (ValueError, TypeError):
            food = None
        if not food:
            raise ValueError("That food was not found.")
        if not unit:
            raise ValueError("Unit is required.")

        if not remove_meal_ingredient(meal, food, unit):
            raise ValueError("That ingredient is no longer part of this meal.")
        messages.success(request, f"{food.name} removed from {meal.name}.")

    except ValueError as error:
        messages.error(request, str(error))
    except IntegrityError:
        logger.exception("Could not remove ingredient")
        messages.error(request, "We couldn't remove that ingredient. Please try again.")

    return redirect(_detail_url(meal.id))


# =========================================================
# DAILY TIMETABLE
# =========================================================

@login_required
def daily_timetable(request):
    """Open the selected day in the weekly timetable."""

    requested_date = request.GET.get("date")

    try:
        if requested_date:
            selected_date = date.fromisoformat(requested_date)
        else:
            selected_date = date.today()
    except ValueError:
        messages.error(request, "Choose a valid date from the calendar.")
        selected_date = date.today()

    return redirect(f"{reverse('meals:timetable')}?date={selected_date.isoformat()}")


# =========================================================
# GENERATE WEEKLY TIMETABLE
# =========================================================

@login_required
def generate_timetable(request):
    """
    Generate a seven-day timetable from available meals.

    This uses the timetable service to avoid unnecessary
    repetition and optionally reuse lunch for supper.
    """

    if request.method != "POST":
        return redirect("meals:timetable")

    requested_date = request.POST.get("date")
    reuse_lunch = request.POST.get("reuse_lunch") == "on"

    try:
        target_date = date.fromisoformat(requested_date) if requested_date else date.today()
    except (ValueError, TypeError):
        messages.error(request, "Choose a valid date for the week.")
        return redirect("meals:timetable")

    try:
        week_start = get_week_start(target_date)

        meals = list(get_user_available_meals(request.user))

        if not meals:
            raise ValueError("There are no active meals available.")

        # At this stage we use the same meal pool.
        # Later this can be replaced by AI/preference-based meal selection.
        meal_options = {meal_type: meals for meal_type in MEAL_TYPES}

        generated = create_weekly_timetable(
            meal_options=meal_options,
            start_date=week_start,
            reuse_lunch=reuse_lunch,
        )

        validate_timetable(generated)

        # Save the generated timetable.
        with transaction.atomic():
            for current_date, meals_for_day in generated.items():
                for meal_type, meal in meals_for_day.items():
                    if meal is not None:
                        schedule_meal(
                            user=request.user,
                            meal=meal,
                            date=current_date,
                            meal_type=meal_type,
                            servings=1,
                        )

        messages.success(request, "Your weekly meal timetable has been generated.")

    except ValueError as error:
        messages.error(request, str(error))

    return redirect("meals:timetable")


# =========================================================
# SCHEDULE MEAL
# =========================================================

@login_required
def schedule_meal_view(request):
    """Schedule a meal for a specific date and meal type."""

    if request.method != "POST":
        return _back_to_week(request)

    meal_id = request.POST.get("meal_id")
    schedule_date = request.POST.get("date")
    meal_type = request.POST.get("meal_type")
    servings = request.POST.get("servings", 1)

    try:
        schedule_date = date.fromisoformat(schedule_date)
    except (ValueError, TypeError):
        messages.error(request, "Choose a valid date for this meal.")
        return _back_to_week(request)

    try:
        servings = int(servings)
    except (ValueError, TypeError):
        messages.error(request, "Enter a whole number of servings.")
        return _back_to_week(request)

    try:
        meal = get_user_available_meal(request.user, meal_id)

        if meal is None:
            raise ValueError("Selected meal was not found.")

        if servings <= 0:
            raise ValueError("Servings must be greater than zero.")

        if meal_type not in MEAL_TYPES:
            raise ValueError("Choose a meal time from the list.")
        if get_user_schedule(request.user).filter(
            date=schedule_date,
            meal_type=meal_type,
        ).exists():
            raise ValueError("That meal-planner slot is already occupied.")

        schedule_meal(
            user=request.user,
            meal=meal,
            date=schedule_date,
            meal_type=meal_type,
            servings=servings,
        )

        messages.success(request, f"{meal.name} scheduled successfully.")

    except (ValueError, TypeError) as error:
        messages.error(request, str(error))

    return _back_to_week(request)


# =========================================================
# UPDATE SERVINGS
# =========================================================

@login_required
def update_meal_servings(request, schedule_id):
    """Update servings for a scheduled meal."""

    if request.method != "POST":
        return _back_to_week(request)

    servings = request.POST.get("servings")

    try:
        servings = int(servings)
    except (ValueError, TypeError):
        messages.error(request, "Enter a whole number of servings.")
        return _back_to_week(request)

    try:
        schedule = update_servings(
            user=request.user,
            schedule_id=schedule_id,
            servings=servings,
        )

        if schedule is None:
            messages.error(request, "Scheduled meal not found.")
        else:
            messages.success(request, "Servings updated successfully.")

    except (ValueError, TypeError) as error:
        messages.error(request, str(error))

    return _back_to_week(request)


# =========================================================
# REMOVE SCHEDULED MEAL
# =========================================================

@login_required
def remove_meal_schedule(request, schedule_id):
    """Remove a meal from the user's timetable."""

    if request.method != "POST":
        return _back_to_week(request)

    try:
        removed = remove_scheduled_meal(
            user=request.user,
            schedule_id=schedule_id,
        )
        if removed:
            messages.success(request, "Meal removed from your timetable.")
        else:
            messages.error(request, "Scheduled meal not found.")
    except ValueError as error:
        messages.error(request, str(error))

    return _back_to_week(request)




# =========================================================
# EDIT MEAL
# =========================================================
def _meal_image_url(meal):
    """Real uploaded image first, legacy `image_url` second, else empty (fallback SVG)."""
    image = getattr(meal, "image", None)
    try:
        if image:
            return image.url
    except ValueError:
        pass
    return getattr(meal, "image_url", "") or ""


def _unit_options(current_unit=""):
    """Form units, plus the ingredient's own unit if it predates the current list."""
    options = list(INGREDIENT_UNITS)
    if current_unit and current_unit not in options:
        options.append(current_unit)
    return options


def _current_ingredients(meal):
    ingredients = get_meal_ingredients(meal)
    if hasattr(ingredients, "select_related"):
        ingredients = ingredients.select_related("food")
    return list(ingredients)


def _rows_from_ingredients(ingredients):
    return [
        {
            "food_id": ing.food_id,
            "food_name": ing.food.name,
            "old_unit": ing.unit,
            "quantity": _fmt_qty(ing.quantity or 0),
            "unit": ing.unit,
            "unit_options": _unit_options(ing.unit),
            "remove": False,
            "error": "",
        }
        for ing in ingredients
    ]


def _form_from_meal(meal):
    def text(value):
        return "" if value is None else str(value)

    return {
        "name": text(meal.name),
        "description": text(getattr(meal, "description", "")),
        "instructions": text(getattr(meal, "instructions", "")),
        "prep_time": text(getattr(meal, "prep_time", "")),
        "cook_time": text(getattr(meal, "cook_time", "")),
    }


def _render_edit_meal(request, meal, form, rows, new_ing=None, errors=None, error_list=None):
    return render(request, "meals/meals.html", {
        "meal": meal,
        "edit_mode": True,
        "form": form,
        "rows": rows,
        "new_ing": new_ing or {"food_id": "", "quantity": "", "unit": ""},
        "errors": errors or {},
        "error_list": error_list or [],
        "foods": get_all_foods(),
        "units": INGREDIENT_UNITS,
        "current_image_url": _meal_image_url(meal),
    })


@login_required
def edit_meal(request, meal_id):
    """
    Edit a meal: details, image and ingredients (change quantity/unit, remove, add one).

    All input is validated first; nothing is saved unless everything is valid, and the
    meal + ingredient changes are applied in one transaction. Existing images are kept
    unless a replacement is uploaded. Food Library records are never modified.

    Note: Meal is not linked to a user in this project (create_meal takes no user), so
    there is no per-user ownership to enforce; edits are limited to logged-in users.
    """

    meal = get_meal_by_id(meal_id)

    if not meal:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    current_list = _current_ingredients(meal)

    if request.method != "POST":
        return _render_edit_meal(request, meal, _form_from_meal(meal), _rows_from_ingredients(current_list))

    # ---------------------------------------------------------- read input
    form = {key: request.POST.get(key, "").strip()
            for key in ("name", "description", "instructions", "prep_time", "cook_time")}
    new_ing = {
        "food_id": request.POST.get("new_food", "").strip(),
        "quantity": request.POST.get("new_qty", "").strip(),
        "unit": request.POST.get("new_unit", "").strip(),
    }
    image = request.FILES.get("image")
    errors = {}

    # ---------------------------------------------------------- meal fields
    if not form["name"]:
        errors["name"] = "Meal name is required."
    elif len(form["name"]) > 150:
        errors["name"] = "Meal name must be 150 characters or fewer."

    times = {}
    for key, label in (("prep_time", "Prep time"), ("cook_time", "Cook time")):
        raw = form[key]
        if not raw:
            times[key] = None
            continue
        try:
            value = int(raw)
        except ValueError:
            errors[key] = f"{label} must be a whole number of minutes."
            continue
        if value < 0:
            errors[key] = f"{label} cannot be negative."
            continue
        times[key] = value

    if image:
        image_error = validate_image_upload(image)
        if image_error:
            errors["image"] = image_error

    # ---------------------------------------------------------- existing ingredients
    # The browser's rows are matched against the meal's real ingredients; nothing the
    # browser sends is trusted on its own.
    current = {(str(ing.food_id), ing.unit): ing for ing in current_list}

    food_ids = request.POST.getlist("row_food")
    old_units = request.POST.getlist("row_old_unit")
    quantities = request.POST.getlist("row_qty")
    units = request.POST.getlist("row_unit")
    removals = set(request.POST.getlist("row_remove"))

    rows, to_remove, to_add, final_keys = [], [], [], set()

    if not (len(food_ids) == len(old_units) == len(quantities) == len(units)):
        errors["ingredients"] = "The ingredient list was incomplete. Please reload the page and try again."
        food_ids = old_units = quantities = units = []

    for index, (food_id, old_unit, qty_raw, unit) in enumerate(zip(food_ids, old_units, quantities, units)):
        ing = current.get((food_id, old_unit))
        row = {
            "food_id": food_id,
            "food_name": ing.food.name if ing else "Unknown food",
            "old_unit": old_unit,
            "quantity": qty_raw,
            "unit": unit,
            "unit_options": _unit_options(unit or old_unit),
            "remove": str(index) in removals,
            "error": "",
        }
        rows.append(row)

        if ing is None:
            row["error"] = "This ingredient is no longer part of the meal. Reload the page."
            continue
        if row["remove"]:
            to_remove.append(ing)
            continue

        try:
            qty = Decimal(qty_raw)
        except (InvalidOperation, TypeError):
            row["error"] = "Enter a valid quantity."
            continue
        if not qty.is_finite() or qty <= 0:
            row["error"] = "Quantity must be greater than zero."
            continue
        if unit != old_unit and unit not in INGREDIENT_UNITS:
            row["error"] = "Choose a unit from the list."
            continue

        key = (ing.food_id, unit)
        if key in final_keys:
            row["error"] = "This food is listed twice with the same unit."
            continue
        final_keys.add(key)

        if qty != ing.quantity or unit != old_unit:
            to_remove.append(ing)                    # change = remove old + add new
            to_add.append((ing.food, qty, unit))

    # ---------------------------------------------------------- optional new ingredient
    if new_ing["food_id"] or new_ing["quantity"]:
        try:
            food = get_food_by_id(new_ing["food_id"]) if new_ing["food_id"] else None
        except (ValueError, TypeError):
            food = None

        try:
            new_qty = Decimal(new_ing["quantity"])
        except (InvalidOperation, TypeError):
            new_qty = None

        if not food:
            errors["new_ingredient"] = "Choose a food for the new ingredient."
        elif new_qty is None or not new_qty.is_finite() or new_qty <= 0:
            errors["new_ingredient"] = "Enter a quantity greater than zero for the new ingredient."
        elif new_ing["unit"] not in INGREDIENT_UNITS:
            errors["new_ingredient"] = "Choose a unit for the new ingredient."
        elif (food.id, new_ing["unit"]) in final_keys:
            errors["new_ingredient"] = f"{food.name} is already on this meal with that unit. Edit its row instead."
        else:
            to_add.append((food, new_qty, new_ing["unit"]))

    # ---------------------------------------------------------- stop on validation errors
    row_errors = [f"{r['food_name']}: {r['error']}" for r in rows if r["error"]]

    if errors or row_errors:
        messages.error(request, "Please fix the highlighted fields and save again.")
        return _render_edit_meal(
            request, meal, form, rows, new_ing, errors,
            error_list=list(errors.values()) + row_errors,
        )

    # ---------------------------------------------------------- save everything together
    try:
        with transaction.atomic():
            meal.name = form["name"]
            meal.description = form["description"]
            meal.instructions = form["instructions"]
            meal.prep_time = times["prep_time"]
            meal.cook_time = times["cook_time"]
            if image:                                # no upload = keep the existing image
                meal.image = image
            meal.save()

            for ing in to_remove:
                remove_meal_ingredient(meal, ing.food, ing.unit)
            for food, qty, unit in to_add:
                add_meal_ingredient(meal, food, qty, unit)

        messages.success(request, f"Meal “{meal.name}” updated successfully.")
        return redirect("meals:meal_detail", meal_id=meal.id)

    except ValueError as error:
        message = str(error)
    except CloudinaryStorageError as error:
        message = str(error)
    except IntegrityError:
        logger.exception("Could not update meal %s", meal_id)
        message = "A meal with that name or ingredient combination already exists."

    messages.error(request, message)
    fresh = get_meal_by_id(meal_id) or meal         # rolled back: show the stored name again
    return _render_edit_meal(request, fresh, form, rows, new_ing, errors, error_list=[message])




@login_required
def meal_library(request):
    """Display active meals that this user has not archived."""

    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "default")
    if sort not in {"default", "az", "za", "time"}:
        sort = "default"

    meals = (
        search_user_available_meals(request.user, query)
        if query
        else get_user_available_meals(request.user)
    )
    meals = _ordered_meals(meals, sort)
    pagination = paginate_queryset(request, meals, 12)
    return render(request, "meals/meals.html", {
        "meals": pagination["page_obj"].object_list,
        "page_obj": pagination["page_obj"],
        "page_links": pagination["page_links"],
        "page_query": pagination["page_query"],
        "pagination_label": "Meal pages",
        "meal_total_count": pagination["page_obj"].paginator.count,
        "query": query,
        "sort": sort,
        "archived_count": get_user_archived_meals(request.user).count(),
        "favorites_count": get_user_favorite_meals(request.user).count(),
    })


@login_required
def archived_meals(request):
    """List meals archived by the current user."""

    archived = get_user_archived_meals(request.user).order_by("name", "pk")
    pagination = paginate_queryset(request, archived, 12)
    return render(request, "meals/meals.html", {
        "archived_meals": pagination["page_obj"].object_list,
        "page_obj": pagination["page_obj"],
        "page_links": pagination["page_links"],
        "page_query": pagination["page_query"],
        "pagination_label": "Archived meal pages",
        "meal_total_count": pagination["page_obj"].paginator.count,
        "archived_count": get_user_archived_meals(request.user).count(),
        "favorites_count": get_user_favorite_meals(request.user).count(),
    })


@login_required
def favorite_meals(request):
    """List active meals this user has favorited."""

    sort = request.GET.get("sort", "default")
    if sort not in {"default", "az", "za", "time"}:
        sort = "default"
    favorites = _ordered_meals(get_user_favorite_meals(request.user), sort)
    pagination = paginate_queryset(request, favorites, 12)
    return render(request, "meals/meals.html", {
        "meals": pagination["page_obj"].object_list,
        "page_obj": pagination["page_obj"],
        "page_links": pagination["page_links"],
        "page_query": pagination["page_query"],
        "pagination_label": "Favorite meal pages",
        "meal_total_count": pagination["page_obj"].paginator.count,
        "sort": sort,
        "favorites_page": True,
        "archived_count": get_user_archived_meals(request.user).count(),
        "favorites_count": get_user_favorite_meals(request.user).count(),
    })


@login_required
def toggle_favorite(request, meal_id):
    if request.method != "POST":
        return redirect("meals:meal_library")

    meal = get_user_available_meal(request.user, meal_id)
    if meal is None:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    preference = get_user_meal_preference(request.user, meal)
    preference.is_favorite = not preference.is_favorite
    preference.save(update_fields=["is_favorite"])
    messages.success(
        request,
        "Added to favorites." if preference.is_favorite else "Removed from favorites.",
    )

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("meals:meal_library")


@login_required
def archive_meal(request, meal_id):
    if request.method != "POST":
        return redirect("meals:meal_library")

    meal = get_user_available_meal(request.user, meal_id)
    if meal is None:
        messages.error(request, "Meal not found.")
        return redirect("meals:meal_library")

    preference = get_user_meal_preference(request.user, meal)
    preference.is_archived = True
    preference.save(update_fields=["is_archived"])
    messages.success(request, f"“{meal.name}” was archived for your account.")
    return redirect("meals:meal_library")


@login_required
def restore_meal(request, meal_id):
    if request.method != "POST":
        return redirect("meals:archived_meals")

    meal = get_user_archived_meals(request.user).filter(pk=meal_id).first()
    if meal is None:
        messages.error(request, "That archived meal was not found.")
        return redirect("meals:archived_meals")

    preference = get_user_meal_preference(request.user, meal)
    preference.is_archived = False
    preference.save(update_fields=["is_archived"])
    messages.success(request, f"“{meal.name}” was restored to your meals.")
    return redirect("meals:archived_meals")
