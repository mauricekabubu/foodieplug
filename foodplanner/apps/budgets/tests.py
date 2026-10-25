from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Budget, BudgetEntry


class BudgetPaginationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="budget-reader",
            email="budget-reader@example.com",
            password="safe-test-password",
        )
        self.client.force_login(self.user)
        today = date.today()
        self.budget = Budget.objects.create(
            user=self.user,
            name="Current food budget",
            amount=Decimal("1000"),
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=10),
        )
        for number in range(17):
            BudgetEntry.objects.create(
                budget=self.budget,
                description=f"Expense {number:02}",
                amount=Decimal("10"),
                date=today,
            )

    def test_budget_detail_paginates_expenses_with_graceful_page_bounds(self):
        response = self.client.get(
            reverse("budgets:detail", args=[self.budget.pk]),
            {"page": "2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(response.context["expense_count"], 17)
        self.assertEqual(len(response.context["expenses"]), 2)
        self.assertEqual(
            response.context["budget_insights"][1],
            "You've recorded 17 expenses, averaging KES 10 each.",
        )

        filtered = self.client.get(
            reverse("budgets:detail", args=[self.budget.pk]),
            {"expense_q": "Expense 16"},
        )
        self.assertEqual(filtered.context["expense_count"], 1)
        self.assertEqual(filtered.context["expenses"][0].description, "Expense 16")

        out_of_range = self.client.get(
            reverse("budgets:detail", args=[self.budget.pk]),
            {"page": "999"},
        )
        self.assertEqual(out_of_range.context["page_obj"].number, 2)


class AddExpenseTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="expense-writer",
            email="expense-writer@example.com",
            password="safe-test-password",
        )
        self.other_user = user_model.objects.create_user(
            username="other-budget-owner",
            email="other-budget-owner@example.com",
            password="safe-test-password",
        )
        self.client.force_login(self.user)
        today = date.today()
        self.budget = Budget.objects.create(
            user=self.user,
            name="Food budget",
            amount=Decimal("1000.00"),
            start_date=today - timedelta(days=5),
            end_date=today + timedelta(days=25),
        )
        self.other_budget = Budget.objects.create(
            user=self.other_user,
            name="Someone else's budget",
            amount=Decimal("500.00"),
            start_date=self.budget.start_date,
            end_date=self.budget.end_date,
        )
        self.url = reverse("budgets:add_expense", args=[self.budget.pk])
        self.valid_data = {
            "description": "Maize flour 2kg",
            "amount": "125.50",
            "date": today.isoformat(),
        }

    def test_valid_expense_saves_once_redirects_and_updates_budget_metrics(self):
        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(
            response,
            reverse("budgets:detail", args=[self.budget.pk]),
            fetch_redirect_response=False,
        )
        self.assertIn(
            "Expense added to your budget.",
            [str(message) for message in get_messages(response.wsgi_request)],
        )
        expense = BudgetEntry.objects.get()
        self.assertEqual(expense.budget, self.budget)
        self.assertEqual(expense.description, "Maize flour 2kg")
        self.assertEqual(expense.amount, Decimal("125.50"))
        self.assertEqual(expense.date, date.fromisoformat(self.valid_data["date"]))

        detail = self.client.get(reverse("budgets:detail", args=[self.budget.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["total_spent"], Decimal("125.50"))
        self.assertEqual(detail.context["remaining_budget"], Decimal("874.50"))
        self.assertEqual(detail.context["budget_usage"], Decimal("12.6"))
        self.assertEqual(detail.context["spending_over_time"][0]["amount"], 125.5)
        self.assertEqual(detail.context["expense_count"], 1)
        self.assertEqual(detail.context["expenses"][0].pk, expense.pk)

    def test_missing_and_invalid_expense_values_are_rejected(self):
        invalid_posts = [
            {},
            {**self.valid_data, "description": "   "},
            {**self.valid_data, "description": "x" * 201},
            {**self.valid_data, "amount": ""},
            {**self.valid_data, "amount": "not-money"},
            {**self.valid_data, "amount": "0"},
            {**self.valid_data, "amount": "-1.00"},
            {**self.valid_data, "amount": "1.001"},
            {**self.valid_data, "amount": "100000000.00"},
            {**self.valid_data, "date": "2026-02-30"},
        ]

        for post_data in invalid_posts:
            with self.subTest(post_data=post_data):
                response = self.client.post(self.url, post_data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(BudgetEntry.objects.count(), 0)

    def test_expense_outside_budget_period_is_rejected(self):
        outside_date = (self.budget.end_date + timedelta(days=1)).isoformat()

        response = self.client.post(self.url, {**self.valid_data, "date": outside_date})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(BudgetEntry.objects.count(), 0)

    def test_unauthenticated_user_cannot_create_expense(self):
        self.client.logout()

        response = self.client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(BudgetEntry.objects.count(), 0)

    def test_user_cannot_create_expense_in_another_users_budget(self):
        response = self.client.post(
            reverse("budgets:add_expense", args=[self.other_budget.pk]),
            self.valid_data,
        )

        self.assertRedirects(
            response,
            reverse("budgets:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(BudgetEntry.objects.count(), 0)


class BudgetOverviewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="overview-reader",
            email="overview-reader@example.com",
            password="safe-test-password",
        )
        self.other_user = user_model.objects.create_user(
            username="private-budget-owner",
            email="private-budget-owner@example.com",
            password="safe-test-password",
        )
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.dashboard_url = reverse("budgets:dashboard")

    def make_budget(self, user=None, *, start=None, end=None, name="Food budget", amount="1000.00"):
        return Budget.objects.create(
            user=user or self.user,
            name=name,
            amount=Decimal(amount),
            start_date=start or self.today,
            end_date=end or self.today + timedelta(days=30),
        )

    def test_newly_created_budget_is_selected_in_overview_using_local_date(self):
        start = self.today
        end = self.today + timedelta(days=30)
        created = self.client.post(
            reverse("budgets:create"),
            {
                "name": "Local date budget",
                "amount": "9000.00",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        budget = Budget.objects.get(user=self.user, name="Local date budget")
        self.assertRedirects(
            created,
            reverse("budgets:detail", args=[budget.pk]),
            fetch_redirect_response=False,
        )

        # Reproduce the former server-date mismatch: the server is still on
        # yesterday while Django's active timezone has moved to the new day.
        with patch("apps.budgets.views.date") as mocked_date:
            mocked_date.today.return_value = self.today - timedelta(days=1)
            response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["budget"], budget)
        self.assertIn(budget, response.context["budgets"])
        self.assertEqual(response.context["total_spent"], Decimal("0"))
        self.assertEqual(response.context["remaining_budget"], Decimal("9000.00"))
        self.assertContains(response, "Local date budget")
        self.assertNotContains(response, "No budget is active today")

    def test_overlapping_budgets_select_latest_start_then_latest_id(self):
        earlier_start = self.today - timedelta(days=4)
        older = self.make_budget(start=earlier_start, name="Older overlap")
        later_start = self.make_budget(start=self.today - timedelta(days=2), name="Later overlap")
        same_start_newer = self.make_budget(start=later_start.start_date, name="Newest overlap")

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.context["budget"], same_start_newer)
        self.assertIn(older, response.context["budgets"])
        self.assertIn(later_start, response.context["budgets"])

    def test_future_and_expired_budgets_remain_listed_but_do_not_fill_overview(self):
        expired = self.make_budget(
            start=self.today - timedelta(days=10),
            end=self.today - timedelta(days=1),
            name="Expired budget",
        )
        future = self.make_budget(
            start=self.today + timedelta(days=1),
            end=self.today + timedelta(days=20),
            name="Future budget",
        )

        response = self.client.get(self.dashboard_url)

        self.assertIsNone(response.context["budget"])
        self.assertIn(expired, response.context["budgets"])
        self.assertIn(future, response.context["budgets"])
        self.assertContains(response, "No budget is active today")
        self.assertContains(response, "Set Budget")

    def test_other_users_budgets_do_not_appear_in_overview_or_list(self):
        private_budget = self.make_budget(
            user=self.other_user,
            name="Private household budget",
        )

        response = self.client.get(self.dashboard_url)

        self.assertIsNone(response.context["budget"])
        self.assertEqual(list(response.context["budgets"]), [])
        self.assertNotContains(response, private_budget.name)
        self.assertContains(response, "No budget yet")
