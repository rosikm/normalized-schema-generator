import unittest
from types import SimpleNamespace
from unittest.mock import patch

from normalized_schema_generator import widget


class FakeSpark:
    def __init__(self):
        self.queries = []

    def sql(self, query):
        self.queries.append(query)
        if query == "SHOW SCHEMAS IN `Sales`":
            rows = [SimpleNamespace(namespace="dbo"), SimpleNamespace(namespace="marketing")]
        elif query == "SHOW TABLES IN `Sales`.`dbo`":
            rows = [SimpleNamespace(tableName="customers")]
        elif query == "SHOW TABLES IN `Sales`.`marketing`":
            rows = [SimpleNamespace(tableName="orders")]
        elif query == "DESCRIBE TABLE `Sales`.`marketing`.`orders`":
            rows = [{"col_name": "order_id", "data_type": "int"}]
        else:
            raise AssertionError(f"Unexpected Spark SQL query: {query}")
        return SimpleNamespace(collect=lambda: rows)


class LakehouseTableTests(unittest.TestCase):
    def test_schema_catalog_fallback_lists_tables_with_schema_paths(self):
        spark = FakeSpark()
        lakehouse_utils = SimpleNamespace(
            listTables=lambda _name: (_ for _ in ()).throw(
                RuntimeError("schemas are unsupported")
            )
        )

        with patch.object(widget, "FABRIC_AVAILABLE", True):
            with patch.object(widget, "_lh_utils", lakehouse_utils):
                with patch.object(widget, "_spark", spark):
                    tables = widget._list_tables("Sales", "lakehouse-id", 2)

        self.assertEqual(
            tables,
            [
                {
                    "name": "dbo.customers",
                    "path": "/lakehouse-id/Tables/dbo/customers",
                    "type": "delta",
                },
                {
                    "name": "marketing.orders",
                    "path": "/lakehouse-id/Tables/marketing/orders",
                    "type": "delta",
                },
            ],
        )

    def test_schema_qualified_table_name_is_used_for_column_lookup(self):
        spark = FakeSpark()

        with patch.object(widget, "FABRIC_AVAILABLE", True):
            with patch.object(widget, "_spark", spark):
                columns = widget._get_columns(
                    "marketing.orders", "Sales", "lakehouse-id", 2
                )

        self.assertEqual(columns, [{"name": "order_id", "dtype": "int"}])
        self.assertIn(
            "DESCRIBE TABLE `Sales`.`marketing`.`orders`",
            spark.queries,
        )

    def test_legacy_listing_keeps_table_names_and_dbo_paths(self):
        lakehouse_utils = SimpleNamespace(
            listTables=lambda _name: [SimpleNamespace(name="events")]
        )

        with patch.object(widget, "FABRIC_AVAILABLE", True):
            with patch.object(widget, "_lh_utils", lakehouse_utils):
                tables = widget._list_tables("Legacy", "lakehouse-id", 2)

        self.assertEqual(
            tables,
            [{
                "name": "events",
                "path": "/lakehouse-id/Tables/dbo/events",
                "type": "delta",
            }],
        )


if __name__ == "__main__":
    unittest.main()
