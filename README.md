# Normalized Schema Generator

An [anywidget](https://anywidget.dev)-based Jupyter widget for **Microsoft Fabric** notebooks that visually configures a normalized schema JSON config for **Process Mining** data import.

Replaces manual JSON authoring by providing dropdowns, toggles, and inline forms to select lakehouses, tables, columns, and FK/PK relationships, then auto-generates the `dataSource` section of the config.

## Install

```bash
pip install normalized-schema-generator
```

## Usage

### In a Fabric notebook

```python
from normalized_schema_generator import NormalizedSchemaWidget

w = NormalizedSchemaWidget()
w
```

The widget auto-detects the Fabric environment and populates lakehouses, tables, and columns using `notebookutils` and Spark.

After configuring your schema, retrieve the generated JSON:

```python
print(w.config_json)
```

### Local development

Outside Fabric, the widget uses mock data automatically — no `notebookutils` or Spark needed:

```bash
pip install normalized-schema-generator jupyterlab
jupyter lab
```

## Features

- **Lakehouse selection** from the current workspace
- **Data source toggle**: Delta Tables or Files (CSV / Parquet)
- **Events table** (required): column checklist with data type badges, include/exclude toggles
- **Cases table** (optional): same column management, with required Events-to-Cases FK relationship
- **Reference management**: add/edit/remove FK joins to lookup tables, with target column selection
- **Auto-generated JSON**: full `inputDataBinding` config with `dataSource`, connection properties, and dataset definitions
- **Copy to clipboard** for the generated JSON
- **Fluent 2 styling** matching the Fabric design language

## JSON output

The widget generates a complete normalized import config following the [Process Mining Normalized Import specification](https://learn.microsoft.com/en-us/power-automate/process-mining-overview):

- `dataSourceSchemaType: 1` (Normalized)
- `dataSourceType: 2` (OneLake)
- Proper `datasets[]` with Kind 0 (Event), 1 (Case), 2 (Join)
- FK columns excluded from `Columns[]` (exposed via `Join[].ExportName`)
- No nested joins on lookup datasets

## License

MIT
