"""
Normalized Schema Generator Widget for Microsoft Fabric Notebooks.

An anywidget-based interactive widget that lets users visually configure
a normalized schema JSON config for Process Mining data import. Replaces
manual JSON authoring by providing dropdowns, toggles, and inline forms.

Usage in a Fabric notebook:
    from normalized_schema_generator import NormalizedSchemaWidget
    w = NormalizedSchemaWidget()
    w  # display the widget

    # After configuration, retrieve the generated JSON:
    print(w.config_json)

For local development (outside Fabric), mock data is used automatically.
"""

import json
import pathlib

import anywidget
import traitlets

# ── Fabric availability detection ────────────────────────────────────────

FABRIC_AVAILABLE = False
_spark = None

try:
    from notebookutils import lakehouse as _lh_utils
    from notebookutils import runtime as _runtime_utils
    from notebookutils import fs as _fs_utils
    FABRIC_AVAILABLE = True
except ImportError:
    _lh_utils = None
    _runtime_utils = None
    _fs_utils = None

try:
    from pyspark.sql import SparkSession
    _spark = SparkSession.builder.getOrCreate()
except Exception:
    pass


# ── Fabric integration / mock helpers ────────────────────────────────────

def _get_fabric_context():
    if FABRIC_AVAILABLE:
        result = {"workspace_id": "", "tenant_id": ""}
        # Workspace ID: from runtime context (Py4J Java Map, bracket access)
        try:
            result["workspace_id"] = str(_runtime_utils.context["currentWorkspaceId"])
        except Exception:
            pass
        # Tenant ID: not in runtime context — use Spark trident config
        if _spark:
            try:
                result["tenant_id"] = _spark.conf.get("trident.tenant.id")
            except Exception:
                pass
        return result
    return {
        "workspace_id": "eb5b82ad-3bfe-4976-9830-107e982eb72f",
        "tenant_id": "a1165230-a86d-4887-9000-276fea697c53",
    }


def _list_lakehouses():
    if FABRIC_AVAILABLE:
        try:
            items = _lh_utils.list()
            return [
                {"id": str(item.id), "displayName": item.displayName}
                for item in items
            ]
        except Exception:
            return []
    return [
        {"id": "2de91b1d-28a0-4bbb-839c-3ba65414497a", "displayName": "ProcessMining_LH"},
        {"id": "f8c3a1b2-1234-5678-9abc-def012345678", "displayName": "Sales_LH"},
        {"id": "a0b1c2d3-aaaa-bbbb-cccc-ddddeeeeeeee", "displayName": "HR_LH"},
    ]


def _list_files_recursive(base_path, lakehouse_id, file_type, prefix="", depth=0, max_depth=3):
    """Recursively list data files under the Files section."""
    results = []
    if depth > max_depth:
        return results
    exts = (".csv",) if file_type == 0 else (".parquet", ".snappy.parquet")
    try:
        items = _fs_utils.ls(base_path)
    except Exception:
        return results
    for item in items:
        name = item.name.rstrip("/")
        if name.startswith("_") or name.startswith("."):
            continue
        rel = f"{prefix}/{name}" if prefix else name
        if item.isDir:
            results.extend(
                _list_files_recursive(item.path, lakehouse_id, file_type, rel, depth + 1, max_depth)
            )
        elif any(name.lower().endswith(ext) for ext in exts):
            results.append({
                "name": rel,
                "path": f"/{lakehouse_id}/Files/{rel}",
                "type": "file",
            })
    return results


def _list_tables(lakehouse_name, lakehouse_id, file_type):
    if FABRIC_AVAILABLE:
        try:
            if file_type == 2:
                tables = _lh_utils.listTables(lakehouse_name)
                return [
                    {
                        "name": t.name,
                        "path": f"/{lakehouse_id}/Tables/dbo/{t.name}",
                        "type": "delta",
                    }
                    for t in tables
                ]
            else:
                return _list_files_recursive("Files", lakehouse_id, file_type)
        except Exception:
            return []
    # Mock data
    if file_type == 2:
        return [
            {"name": "events", "path": f"/{lakehouse_id}/Tables/dbo/events", "type": "delta"},
            {"name": "cases", "path": f"/{lakehouse_id}/Tables/dbo/cases", "type": "delta"},
            {"name": "activity", "path": f"/{lakehouse_id}/Tables/dbo/activity", "type": "delta"},
            {"name": "resource", "path": f"/{lakehouse_id}/Tables/dbo/resource", "type": "delta"},
            {"name": "vendors", "path": f"/{lakehouse_id}/Tables/dbo/vendors", "type": "delta"},
            {"name": "departments", "path": f"/{lakehouse_id}/Tables/dbo/departments", "type": "delta"},
        ]
    else:
        return [
            {"name": "events", "path": f"/{lakehouse_id}/Files/events", "type": "folder"},
            {"name": "cases", "path": f"/{lakehouse_id}/Files/cases", "type": "folder"},
            {"name": "activity", "path": f"/{lakehouse_id}/Files/activity", "type": "folder"},
            {"name": "resource", "path": f"/{lakehouse_id}/Files/resource", "type": "folder"},
        ]


def _spark_type_to_simple(dtype_str):
    dtype_str = dtype_str.lower().strip()
    # Integer family
    if dtype_str in ("int", "integer", "bigint", "long", "short", "smallint", "tinyint", "byte"):
        return "int"
    # Float family
    if dtype_str in ("float", "double", "decimal", "numeric", "real", "number") or dtype_str.startswith("decimal"):
        return "double"
    # String family
    if dtype_str in ("string", "varchar", "char", "nvarchar", "text", "nchar") or dtype_str.startswith("varchar") or dtype_str.startswith("char") or dtype_str.startswith("nvarchar"):
        return "string"
    # Boolean
    if dtype_str in ("boolean", "bool", "bit"):
        return "boolean"
    # Date/time family
    if dtype_str in ("timestamp", "timestamp_ntz", "timestamp_ltz", "date", "datetime", "datetime2", "datetimeoffset", "time"):
        return "timestamp"
    # Binary / other — map to string for display
    if dtype_str in ("binary", "varbinary", "array", "map", "struct", "void", "null"):
        return "string"
    return dtype_str


def _get_columns(table_name, lakehouse_name, lakehouse_id, file_type):
    if FABRIC_AVAILABLE and _spark:
        try:
            if file_type == 2:
                full_name = f"`{lakehouse_name}`.`{table_name}`"
                df = _spark.sql(f"DESCRIBE TABLE {full_name}")
                rows = df.collect()
                return [
                    {"name": r["col_name"], "dtype": _spark_type_to_simple(r["data_type"])}
                    for r in rows
                    if r["col_name"] and not r["col_name"].startswith("#")
                ]
            else:
                base = f"Files/{table_name}"
                if file_type == 0:
                    df = _spark.read.option("header", "true").option("inferSchema", "true").csv(base)
                else:
                    df = _spark.read.parquet(base)
                return [
                    {"name": f.name, "dtype": _spark_type_to_simple(f.dataType.simpleString())}
                    for f in df.schema.fields
                ]
        except Exception:
            return []
    # Mock data
    _mock = {
        "events": [
            {"name": "CaseID", "dtype": "int"},
            {"name": "Activity_id", "dtype": "int"},
            {"name": "Resource_id", "dtype": "int"},
            {"name": "StartTimestamp", "dtype": "timestamp"},
            {"name": "EndTimestamp", "dtype": "timestamp"},
            {"name": "EventCost", "dtype": "double"},
        ],
        "cases": [
            {"name": "CaseID", "dtype": "int"},
            {"name": "CustomerName", "dtype": "string"},
            {"name": "CustomerSegment", "dtype": "string"},
            {"name": "InvoiceTotalAmountWithoutVAT", "dtype": "double"},
            {"name": "Region", "dtype": "string"},
        ],
        "activity": [
            {"name": "Activity_id", "dtype": "int"},
            {"name": "Activity", "dtype": "string"},
        ],
        "resource": [
            {"name": "Resource_id", "dtype": "int"},
            {"name": "Resource", "dtype": "string"},
        ],
        "vendors": [
            {"name": "Vendor_id", "dtype": "int"},
            {"name": "VendorName", "dtype": "string"},
            {"name": "VendorCountry", "dtype": "string"},
        ],
        "departments": [
            {"name": "Dept_id", "dtype": "int"},
            {"name": "DeptName", "dtype": "string"},
        ],
    }
    return _mock.get(table_name, [
        {"name": "id", "dtype": "int"},
        {"name": "value", "dtype": "string"},
    ])


# ── Widget class ─────────────────────────────────────────────────────────

_HERE = pathlib.Path(__file__).parent


class NormalizedSchemaWidget(anywidget.AnyWidget):
    _esm = _HERE / "widget.js"
    _css = _HERE / "widget.css"

    # Lakehouse & source config
    lakehouses = traitlets.List([]).tag(sync=True)
    selected_lakehouse_id = traitlets.Unicode("").tag(sync=True)
    selected_lakehouse_name = traitlets.Unicode("").tag(sync=True)
    data_source_file_type = traitlets.Int(2).tag(sync=True)
    available_tables = traitlets.List([]).tag(sync=True)

    # Events table
    events_table_name = traitlets.Unicode("").tag(sync=True)
    events_columns = traitlets.List([]).tag(sync=True)
    events_joins = traitlets.List([]).tag(sync=True)

    # Cases table
    cases_enabled = traitlets.Bool(False).tag(sync=True)
    cases_table_name = traitlets.Unicode("").tag(sync=True)
    cases_columns = traitlets.List([]).tag(sync=True)
    cases_joins = traitlets.List([]).tag(sync=True)
    # Events→Cases FK relationship: {fk_column, pk_column, join_key_type}
    cases_relationship = traitlets.Dict({}).tag(sync=True)

    # Communication & output
    fetch_columns_request = traitlets.Unicode("").tag(sync=True)
    fetch_columns_response = traitlets.List([]).tag(sync=True)
    config_json = traitlets.Unicode("").tag(sync=True)
    loading_status = traitlets.Unicode("").tag(sync=True)
    error_message = traitlets.Unicode("").tag(sync=True)

    # Fabric context
    workspace_id = traitlets.Unicode("").tag(sync=True)
    tenant_id = traitlets.Unicode("").tag(sync=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        ctx = _get_fabric_context()
        self.workspace_id = ctx["workspace_id"]
        self.tenant_id = ctx["tenant_id"]
        self.lakehouses = _list_lakehouses()

    # ── Observers ────────────────────────────────────────

    @traitlets.observe("selected_lakehouse_id")
    def _on_lakehouse_change(self, change):
        lh_id = change["new"]
        if not lh_id:
            self.available_tables = []
            return
        self.loading_status = "Loading tables..."
        self.events_table_name = ""
        self.events_columns = []
        self.events_joins = []
        self.cases_table_name = ""
        self.cases_columns = []
        self.cases_joins = []
        self.cases_relationship = {}
        try:
            self.available_tables = _list_tables(
                self.selected_lakehouse_name, lh_id, self.data_source_file_type
            )
        except Exception as e:
            self.error_message = f"Failed to load tables: {e}"
            self.available_tables = []
        self.loading_status = ""
        self._generate_config()

    @traitlets.observe("data_source_file_type")
    def _on_file_type_change(self, change):
        if not self.selected_lakehouse_id:
            return
        self.loading_status = "Reloading tables..."
        self.events_table_name = ""
        self.events_columns = []
        self.events_joins = []
        self.cases_table_name = ""
        self.cases_columns = []
        self.cases_joins = []
        self.cases_relationship = {}
        try:
            self.available_tables = _list_tables(
                self.selected_lakehouse_name,
                self.selected_lakehouse_id,
                change["new"],
            )
        except Exception as e:
            self.error_message = f"Failed to load tables: {e}"
            self.available_tables = []
        self.loading_status = ""
        self._generate_config()

    @traitlets.observe("events_table_name")
    def _on_events_table_change(self, change):
        name = change["new"]
        if not name:
            self.events_columns = []
            self._generate_config()
            return
        self.loading_status = "Loading columns..."
        try:
            raw = _get_columns(
                name, self.selected_lakehouse_name,
                self.selected_lakehouse_id, self.data_source_file_type,
            )
            self.events_columns = [
                {"name": c["name"], "dtype": c["dtype"], "included": True}
                for c in raw
            ]
        except Exception as e:
            self.error_message = f"Failed to load columns: {e}"
            self.events_columns = []
        self.loading_status = ""
        self._generate_config()

    @traitlets.observe("cases_table_name")
    def _on_cases_table_change(self, change):
        name = change["new"]
        if not name:
            self.cases_columns = []
            self.cases_relationship = {}
            self._generate_config()
            return
        self.cases_relationship = {}
        self.loading_status = "Loading columns..."
        try:
            raw = _get_columns(
                name, self.selected_lakehouse_name,
                self.selected_lakehouse_id, self.data_source_file_type,
            )
            self.cases_columns = [
                {"name": c["name"], "dtype": c["dtype"], "included": True}
                for c in raw
            ]
        except Exception as e:
            self.error_message = f"Failed to load columns: {e}"
            self.cases_columns = []
        self.loading_status = ""
        self._generate_config()

    @traitlets.observe("fetch_columns_request")
    def _on_fetch_columns_request(self, change):
        req = change["new"]
        if not req:
            return
        # Format: "timestamp:tablename"
        parts = req.split(":", 1)
        if len(parts) < 2:
            return
        table_name = parts[1]
        try:
            raw = _get_columns(
                table_name, self.selected_lakehouse_name,
                self.selected_lakehouse_id, self.data_source_file_type,
            )
            self.fetch_columns_response = [
                {"name": c["name"], "dtype": c["dtype"]}
                for c in raw
            ]
        except Exception as e:
            self.error_message = f"Failed to load columns for {table_name}: {e}"
            self.fetch_columns_response = []

    @traitlets.observe("events_columns", "events_joins", "cases_enabled",
                       "cases_columns", "cases_joins", "cases_relationship")
    def _on_data_change(self, change):
        self._generate_config()

    # ── JSON generation ──────────────────────────────────

    def _find_table_path(self, table_name):
        for t in self.available_tables:
            if t["name"] == table_name:
                return t["path"]
        return ""

    def _generate_config(self):
        self.error_message = ""
        if not self.selected_lakehouse_id or not self.events_table_name:
            self.config_json = ""
            return

        try:
            datasets = []
            join_dataset_names = set()

            # ── Events→Cases relationship join ──
            cases_rel = self.cases_relationship
            has_cases_rel = (
                self.cases_enabled
                and self.cases_table_name
                and cases_rel.get("fk_column")
                and cases_rel.get("pk_column")
            )

            # ── Events dataset (Kind=0) ──
            events_fk_cols = {j["source_column"] for j in self.events_joins}
            if has_cases_rel:
                events_fk_cols.add(cases_rel["fk_column"])
            events_columns = [
                {"Name": c["name"]}
                for c in self.events_columns
                if c.get("included") and c["name"] not in events_fk_cols
            ]
            events_join_arr = []
            # Add the Events→Cases join first
            if has_cases_rel:
                events_join_arr.append({
                    "SourceColumnName": cases_rel["fk_column"],
                    "TargetColumnName": cases_rel["pk_column"],
                    "TargetDatasetName": self.cases_table_name,
                    "JoinKeyType": cases_rel.get("join_key_type", "Integer"),
                })
            for j in self.events_joins:
                entry = {
                    "SourceColumnName": j["source_column"],
                    "TargetColumnName": j["target_column"],
                    "TargetDatasetName": j["target_table"],
                    "JoinKeyType": j["join_key_type"],
                }
                if j.get("export_name"):
                    entry["ExportName"] = j["export_name"]
                events_join_arr.append(entry)
                join_dataset_names.add(j["target_table"])

            datasets.append({
                "Kind": 0,
                "Name": self.events_table_name,
                "Path": self._find_table_path(self.events_table_name),
                "Columns": events_columns,
                "Join": events_join_arr if events_join_arr else None,
            })

            # ── Cases dataset (Kind=1) ──
            if self.cases_enabled and self.cases_table_name:
                cases_fk_cols = {j["source_column"] for j in self.cases_joins}
                cases_columns = [
                    {"Name": c["name"]}
                    for c in self.cases_columns
                    if c.get("included") and c["name"] not in cases_fk_cols
                ]
                cases_join_arr = []
                for j in self.cases_joins:
                    entry = {
                        "SourceColumnName": j["source_column"],
                        "TargetColumnName": j["target_column"],
                        "TargetDatasetName": j["target_table"],
                        "JoinKeyType": j["join_key_type"],
                    }
                    if j.get("export_name"):
                        entry["ExportName"] = j["export_name"]
                    cases_join_arr.append(entry)
                    join_dataset_names.add(j["target_table"])

                datasets.append({
                    "Kind": 1,
                    "Name": self.cases_table_name,
                    "Path": self._find_table_path(self.cases_table_name),
                    "Columns": cases_columns,
                    "Join": cases_join_arr if cases_join_arr else None,
                })

            # ── Join datasets (Kind=2) ──
            join_table_cols = {}
            for j in list(self.events_joins) + list(self.cases_joins):
                tname = j["target_table"]
                if tname not in join_table_cols:
                    join_table_cols[tname] = {}
                for c in j.get("target_columns", []):
                    if c["name"] not in join_table_cols[tname]:
                        join_table_cols[tname][c["name"]] = c
                    elif c.get("included"):
                        join_table_cols[tname][c["name"]]["included"] = True

            for tname in sorted(join_dataset_names):
                cols_map = join_table_cols.get(tname, {})
                join_cols = [
                    {"Name": c["name"]}
                    for c in cols_map.values()
                    if c.get("included")
                ]
                datasets.append({
                    "Kind": 2,
                    "Name": tname,
                    "Path": self._find_table_path(tname),
                    "Columns": join_cols,
                    "Join": None,
                })

            # ── Assemble full config ──
            config = {
                "inputDataBinding": {
                    "productFlavor": "processadvisor",
                    "dataSource": {
                        "dataSourceSchemaType": 1,
                        "dataSourceType": 2,
                        "dataSourceFileType": self.data_source_file_type,
                        "dataSourceId": "oneLakeDataSourceIdPlaceholder",
                        "path": "",
                        "oneLakeConnectionSetupProperties": {
                            "tenantId": self.tenant_id,
                            "workspaceId": self.workspace_id,
                            "lakehouseId": self.selected_lakehouse_id,
                        },
                        "datasets": datasets,
                    },
                    "miningMetadata": {
                        "ImportConfiguration": {"Attributes": []},
                        "Views": [],
                        "ProcessExtendedMetadata": {
                            "CalculatedMetrics": [],
                            "BusinessRules": [],
                            "ProcessMapHierarchies": [],
                            "CaseCategorization": {
                                "CaseStates": [],
                                "CaseAnnotations": [],
                            },
                        },
                        "ReportData": {},
                        "Calendars": [],
                    },
                },
                "reportConfiguration": {"isEmbeddedReport": True},
            }

            self.config_json = json.dumps(config, indent=2)

        except Exception as e:
            self.error_message = f"Config generation error: {e}"
            self.config_json = ""
