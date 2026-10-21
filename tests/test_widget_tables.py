import io
import json
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

with patch.dict("sys.modules", {"pyspark.sql": None}):
    from normalized_schema_generator import widget


class LakehouseTableTests(unittest.TestCase):
    def setUp(self):
        self.lakehouse = Mock()
        self.lakehouse.listTables.side_effect = RuntimeError(
            "UnsupportedOperationForSchemasEnabledLakehouse"
        )
        self.lakehouse.list.return_value = [
            SimpleNamespace(id="lakehouse-id", displayName="Sales"),
        ]
        self.spark = Mock()
        self.spark.sql.side_effect = RuntimeError(
            "[SCHEMA_NOT_FOUND] The schema spark_catalog cannot be found."
        )
        self.credentials = Mock()
        self.credentials.getToken.return_value = "test-access-token"
        patches = {
            "FABRIC_AVAILABLE": True,
            "_spark": self.spark,
            "_lh_utils": self.lakehouse,
            "_credentials_utils": self.credentials,
            "_get_fabric_context": Mock(return_value={
                "workspace_id": "workspace-id", "tenant_id": "tenant-id",
            }),
        }
        for name, value in patches.items():
            self.enter_patch(name, value)
        self.urlopen = self.enter_patch("urlopen", Mock())

    def enter_patch(self, name, value):
        patcher = patch.object(widget, name, value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def respond(self, *payloads):
        self.urlopen.side_effect = [
            io.BytesIO(json.dumps(payload).encode("utf-8"))
            for payload in payloads
        ]

    def request_query(self, index=0):
        request = self.urlopen.call_args_list[index].args[0]
        return parse_qs(urlsplit(request.full_url).query)

    def new_widget(self):
        instance = widget.NormalizedSchemaWidget()
        self.addCleanup(instance.close)
        return instance

    def test_schema_discovery_does_not_use_incompatible_spark_catalog(self):
        self.respond(
            {"schemas": [{"name": "bronze"}, {"name": "dbo"}]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
        )

        tables = widget._list_tables("Sales", "lakehouse-id", 2)

        self.assertEqual(tables, [
            {
                "name": "bronze.events",
                "path": "/lakehouse-id/Tables/bronze/events",
                "type": "delta",
            },
            {
                "name": "dbo.events",
                "path": "/lakehouse-id/Tables/dbo/events",
                "type": "delta",
            },
        ])
        self.spark.sql.assert_not_called()
        self.credentials.getToken.assert_called_with("storage")
        request = self.urlopen.call_args_list[0].args[0]
        self.assertEqual(urlsplit(request.full_url).path,
            "/delta/workspace-id/lakehouse-id/api/2.1/unity-catalog/schemas")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-access-token")
        self.assertEqual(self.request_query(), {"catalog_name": ["lakehouse-id"]})
        self.assertEqual(self.request_query(1)["schema_name"], ["bronze"])
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 30)

    def test_empty_notebookutils_listing_also_uses_onelake(self):
        self.lakehouse.listTables.side_effect = None
        self.lakehouse.listTables.return_value = []
        self.respond(
            {"schemas": [{"name": "bronze"}]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
        )
        self.assertEqual(
            widget._list_tables("Sales", "lakehouse-id", 2)[0]["name"],
            "bronze.events",
        )

    def test_schema_and_table_pagination(self):
        self.respond(
            {"schemas": [{"name": "bronze"}], "next_page_token": "schema page 2"},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}],
             "next_page_token": "table page +2"},
            {"tables": [{"name": "cases", "data_source_format": "DELTA"}]},
            {"schemas": [{"name": "silver"}]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
        )
        tables = widget._list_tables("Sales", "lakehouse-id", 2)

        self.assertEqual([table["name"] for table in tables],
                         ["bronze.events", "bronze.cases", "silver.events"])
        self.assertEqual(self.request_query(2)["page_token"], ["table page +2"])
        self.assertEqual(self.request_query(3)["page_token"], ["schema page 2"])
        self.assertNotIn("page_token", self.request_query(4))

    def test_repeated_pagination_token_raises_instead_of_looping(self):
        self.respond(
            {"schemas": [], "next_page_token": "same"},
            {"schemas": [], "next_page_token": "same"},
        )
        with self.assertRaisesRegex(RuntimeError, "invalid page token"):
            widget._list_tables("Sales", "lakehouse-id", 2)

    def test_empty_lakehouse_is_a_valid_empty_list(self):
        self.respond({"schemas": [{"name": "dbo"}]}, {"tables": []})
        self.assertEqual(widget._list_tables("Sales", "lakehouse-id", 2), [])

    def test_invalid_metadata_is_not_treated_as_empty_lakehouse(self):
        for payload in ([], {}, {"schemas": None}, {"schemas": ["dbo"]}):
            with self.subTest(payload=payload):
                self.respond(payload)
                with self.assertRaisesRegex(RuntimeError, "invalid"):
                    widget._list_tables("Sales", "lakehouse-id", 2)

    def test_http_error_is_actionable_and_does_not_expose_token(self):
        self.urlopen.side_effect = HTTPError(
            "https://onelake.table.fabric.microsoft.com", 403, "Forbidden", {}, None
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as caught:
            widget._list_tables("Sales", "lakehouse-id", 2)
        self.assertNotIn("test-access-token", str(caught.exception))

    def test_connection_error_is_not_an_empty_list(self):
        self.urlopen.side_effect = URLError("Connection refused")
        with self.assertRaisesRegex(RuntimeError, "Unable to connect"):
            widget._list_tables("Sales", "lakehouse-id", 2)

    def test_missing_workspace_or_credentials_fail_explicitly(self):
        with patch.object(widget, "_get_fabric_context", return_value={
            "workspace_id": "", "tenant_id": "",
        }):
            with self.assertRaisesRegex(RuntimeError, "workspace ID"):
                widget._list_tables("Sales", "lakehouse-id", 2)
        with patch.object(widget, "_credentials_utils", None):
            with self.assertRaisesRegex(RuntimeError, "credentials"):
                widget._list_tables("Sales", "lakehouse-id", 2)
        self.urlopen.assert_not_called()

    def test_missing_access_token_fails_before_request(self):
        self.credentials.getToken.return_value = ""
        with self.assertRaisesRegex(RuntimeError, "access token"):
            widget._list_tables("Sales", "lakehouse-id", 2)
        self.urlopen.assert_not_called()

    def test_schema_columns_do_not_depend_on_spark(self):
        self.respond({"columns": [
            {"name": "CaseID", "type_text": None, "type_name": "long"},
            {"name": "Cost", "type_text": "decimal(18,2)", "type_name": "decimal"},
            {"name": "Activity", "type_name": "string"},
            {"name": "Started", "type_name": "timestamp"},
        ]})
        with patch.object(widget, "_spark", None):
            columns = widget._get_columns(
                "bronze.events", "Sales", "lakehouse-id", 2
            )
        self.assertEqual(columns, [
            {"name": "CaseID", "dtype": "int"},
            {"name": "Cost", "dtype": "double"},
            {"name": "Activity", "dtype": "string"},
            {"name": "Started", "dtype": "timestamp"},
        ])
        request = self.urlopen.call_args.args[0]
        self.assertTrue(urlsplit(request.full_url).path.endswith(
            "/tables/lakehouse-id.bronze.events"
        ))
        self.assertEqual(self.request_query()["schema_name"], ["bronze"])
        self.spark.sql.assert_not_called()

    def test_missing_columns_fail_explicitly(self):
        self.respond({"columns": None})
        with self.assertRaisesRegex(RuntimeError, "did not return table columns"):
            widget._get_columns("bronze.events", "Sales", "lakehouse-id", 2)

    def test_legacy_listing_does_not_call_onelake(self):
        self.lakehouse.listTables.side_effect = None
        self.lakehouse.listTables.return_value = [SimpleNamespace(name="events")]

        self.assertEqual(widget._list_tables("Legacy", "lakehouse-id", 2), [{
            "name": "events",
            "path": "/lakehouse-id/Tables/dbo/events",
            "type": "delta",
        }])
        self.urlopen.assert_not_called()
        self.spark.sql.assert_not_called()

    def test_legacy_columns_preserve_spark_identifier_escaping(self):
        self.spark.sql.side_effect = None
        self.spark.sql.return_value.collect.return_value = [
            {"col_name": "CaseID", "data_type": "bigint"},
        ]
        self.assertEqual(widget._get_columns("events", "Sales`LH", "lakehouse-id", 2),
                         [{"name": "CaseID", "dtype": "int"}])
        self.spark.sql.assert_called_once_with("DESCRIBE TABLE `Sales``LH`.`events`")
        self.urlopen.assert_not_called()

    def test_legacy_column_errors_propagate(self):
        with self.assertRaisesRegex(RuntimeError, "SCHEMA_NOT_FOUND"):
            widget._get_columns("events", "Sales", "lakehouse-id", 2)

    def test_table_error_survives_config_generation_and_recovers_on_reload(self):
        instance = self.new_widget()
        self.urlopen.side_effect = HTTPError(
            "https://onelake.table.fabric.microsoft.com", 403, "Forbidden", {}, None
        )
        instance.selected_lakehouse_name = "Stale selection"
        instance.selected_lakehouse_id = "lakehouse-id"
        self.assertEqual(instance.selected_lakehouse_name, "Sales")
        self.assertEqual(instance.available_tables, [])
        self.assertIn("HTTP 403", instance.error_message)
        self.assertEqual(instance.loading_status, "")
        instance._generate_config()
        self.assertIn("HTTP 403", instance.error_message)
        self.assertEqual(instance.config_json, "")

        self.respond(
            {"schemas": [{"name": "bronze"}]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
        )
        instance._on_lakehouse_change({"new": "lakehouse-id"})
        self.assertEqual(instance.error_message, "")
        self.assertEqual(instance.available_tables[0]["name"], "bronze.events")

    def test_source_type_reload_error_remains_visible(self):
        instance = self.new_widget()
        with patch.object(widget, "_list_tables", return_value=[]):
            instance.selected_lakehouse_id = "lakehouse-id"
        with patch.object(widget, "_list_tables", side_effect=RuntimeError("Access denied")):
            instance.data_source_file_type = 1
        self.assertIn("Access denied", instance.error_message)
        self.assertEqual(instance.loading_status, "")

    def test_column_errors_remain_visible_for_events_cases_and_references(self):
        for target in ("events", "cases", "reference"):
            with self.subTest(target=target):
                instance = self.new_widget()
                with patch.object(widget, "_list_tables", return_value=[]):
                    instance.selected_lakehouse_id = "lakehouse-id"
                with patch.object(widget, "_get_columns",
                                  side_effect=RuntimeError("Metadata denied")):
                    if target == "reference":
                        instance.fetch_columns_response = [{"name": "stale"}]
                        instance.fetch_columns_request = "1:bronze.activity"
                        self.assertEqual(instance.fetch_columns_response, [])
                    else:
                        setattr(instance, f"{target}_table_name", "bronze.events")
                        self.assertEqual(getattr(instance, f"{target}_columns"), [])
                instance._generate_config()
                self.assertIn("Metadata denied", instance.error_message)
                self.assertEqual(instance.config_json, "")
                self.assertEqual(instance.loading_status, "")

    def test_schema_selection_generates_qualified_dataset_and_join_paths(self):
        instance = self.new_widget()
        self.respond(
            {"schemas": [{"name": "bronze"}, {"name": "silver"}]},
            {"tables": [
                {"name": "events", "data_source_format": "DELTA"},
                {"name": "cases", "data_source_format": "DELTA"},
            ]},
            {"tables": [{"name": "events", "data_source_format": "DELTA"}]},
            {"columns": [{"name": "CaseID", "type_name": "long"}]},
            {"columns": [{"name": "CaseID", "type_name": "long"}]},
            {"columns": [{"name": "CaseID", "type_name": "long"}]},
        )
        instance.selected_lakehouse_id = "lakehouse-id"
        instance.events_table_name = "bronze.events"
        instance.cases_enabled = True
        instance.cases_table_name = "bronze.cases"
        instance.cases_relationship = {"fk_column": "CaseID", "pk_column": "CaseID"}
        instance.fetch_columns_request = "1:silver.events"
        instance.events_joins = [{
            "target_table": "silver.events", "source_column": "CaseID",
            "target_column": "CaseID", "join_key_type": "Integer",
            "target_columns": [{"name": "CaseID", "dtype": "int", "included": True}],
        }]

        self.assertEqual(instance.error_message, "")
        config = json.loads(instance.config_json)
        datasets = config["inputDataBinding"]["dataSource"]["datasets"]
        self.assertEqual([(d["Name"], d["Path"]) for d in datasets], [
            ("bronze.events", "/lakehouse-id/Tables/bronze/events"),
            ("bronze.cases", "/lakehouse-id/Tables/bronze/cases"),
            ("silver.events", "/lakehouse-id/Tables/silver/events"),
        ])
        self.assertEqual([j["TargetDatasetName"] for j in datasets[0]["Join"]],
                         ["bronze.cases", "silver.events"])
        self.spark.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
