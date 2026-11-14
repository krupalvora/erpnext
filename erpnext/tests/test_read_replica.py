from unittest.mock import patch

import frappe
from frappe.utils import add_months, today

from erpnext.tests.utils import ERPNextTestSuite

COMPANY = "_Test Company"


class TestReadsOnReplica(ERPNextTestSuite):
	"""Read-only endpoints run on the replica, when one is configured.

	The test database stands in for the replica, reached through a second connection,
	so the endpoints are called with records the test bootstrap has committed."""

	def tearDown(self):
		# a request gets a fresh frappe.local, tests share one
		for connection in ("primary_db", "replica_db"):
			if hasattr(frappe.local, connection):
				delattr(frappe.local, connection)
		super().tearDown()

	def assertReadsOnReplica(self, endpoint, **kwargs):
		"""Assert `endpoint` reads on a replica connection and leaves the primary one in place."""
		read_connections = []
		connect_replica = frappe.connect_replica

		def spy():
			switched = connect_replica()
			read_connections.append(frappe.local.db)
			return switched

		primary = frappe.local.db
		replica_conf = {
			"read_from_replica": 1,
			"replica_host": frappe.conf.db_host or "127.0.0.1",
			"replica_db_port": frappe.conf.db_port,
		}
		with (
			patch.dict(frappe.local.conf, replica_conf),
			patch.object(frappe, "connect_replica", spy),
		):
			result = endpoint(**kwargs)

		self.assertEqual(len(read_connections), 1, f"{endpoint.__module__} did not read on the replica")
		self.assertIsNot(read_connections[0], primary)
		self.assertIs(frappe.local.db, primary)
		return result

	def test_account_tree(self):
		from erpnext.accounts.utils import get_account_balances, get_account_balances_coa, get_children

		self.assertReadsOnReplica(get_children, doctype="Account", parent="", company=COMPANY, is_root=True)
		self.assertReadsOnReplica(
			get_account_balances,
			accounts=[{"value": f"Cash - {frappe.get_cached_value('Company', COMPANY, 'abbr')}"}],
			company=COMPANY,
		)
		self.assertReadsOnReplica(get_account_balances_coa, company=COMPANY)

	def test_cost_center_tree(self):
		from erpnext.accounts.utils import get_children

		self.assertReadsOnReplica(
			get_children, doctype="Cost Center", parent="", company=COMPANY, is_root=True
		)

	def test_warehouse_tree(self):
		from erpnext.stock.doctype.warehouse.warehouse import get_children

		self.assertReadsOnReplica(get_children, doctype="Warehouse", company=COMPANY, is_root=True)

	def test_stock_dashboards(self):
		from erpnext.stock.dashboard import item_dashboard, warehouse_capacity_dashboard

		self.assertReadsOnReplica(item_dashboard.get_data)
		self.assertReadsOnReplica(warehouse_capacity_dashboard.get_data)

	def test_sales_funnel(self):
		from erpnext.selling.page.sales_funnel import sales_funnel

		filters = {"from_date": add_months(today(), -12), "to_date": today(), "company": COMPANY}
		for endpoint in (
			sales_funnel.get_funnel_data,
			sales_funnel.get_pipeline_data,
			sales_funnel.get_opp_by_utm_source,
			sales_funnel.get_opp_by_utm_campaign,
			sales_funnel.get_opp_by_utm_medium,
		):
			with self.subTest(endpoint=endpoint.__name__):
				self.assertReadsOnReplica(endpoint, **filters)

	def test_stock_closing_check(self):
		from erpnext.stock.report.serial_and_batch_wise_stock_balance.serial_and_batch_wise_stock_balance import (
			has_stock_closing_entry_before,
		)

		self.assertReadsOnReplica(has_stock_closing_entry_before, from_date=today(), company=COMPANY)

	def test_chart_sources(self):
		from erpnext.accounts.dashboard_chart_source.account_balance_timeline import (
			account_balance_timeline,
		)
		from erpnext.stock.dashboard_chart_source.stock_value_by_item_group import stock_value_by_item_group

		self.assertReadsOnReplica(
			stock_value_by_item_group.get,
			chart_name="Stock Value by Item Group",
			filters={"company": COMPANY},
			no_cache=1,
		)
		abbr = frappe.get_cached_value("Company", COMPANY, "abbr")
		self.assertReadsOnReplica(
			account_balance_timeline.get,
			chart={"timespan": "Last Year", "time_interval": "Monthly", "filters_json": "{}"},
			filters={"company": COMPANY, "account": f"Cash - {abbr}"},
			no_cache=1,
		)

	def test_chart_sync_time_is_written_on_primary(self):
		"""The chart's data is read on the replica, but `cache_source` records the sync on the primary."""
		from erpnext.stock.dashboard_chart_source.warehouse_wise_stock_value import warehouse_wise_stock_value

		chart = "Warehouse wise Stock Value"
		frappe.db.set_value("Dashboard Chart", chart, "last_synced_on", None, update_modified=False)

		self.assertReadsOnReplica(
			warehouse_wise_stock_value.get,
			chart_name=chart,
			filters={"company": COMPANY},
			refresh=1,
		)

		self.assertTrue(frappe.db.get_value("Dashboard Chart", chart, "last_synced_on"))
