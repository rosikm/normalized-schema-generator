export function render({ model, el }) {
  const root = document.createElement("div");
  root.className = "nsw";
  el.appendChild(root);

  /* ── helpers ──────────────────────────────────────────── */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null) continue;
        if (k === "className") el.className = v;
        else if (k === "textContent") el.textContent = v;
        else if (k === "innerHTML") el.innerHTML = v;
        else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
        else el.setAttribute(k, v);
      }
    }
    for (const child of children.flat()) {
      if (child == null) continue;
      if (typeof child === "string") el.appendChild(document.createTextNode(child));
      else el.appendChild(child);
    }
    return el;
  }

  function dtypeBadge(dtype) {
    const known = { int: "INT", double: "FLOAT", string: "STR", timestamp: "DATE", boolean: "BOOL" };
    const cls = known[dtype] ? dtype : "other";
    return h("span", { className: "nsw-col-dtype " + cls, textContent: known[dtype] || dtype.toUpperCase().slice(0, 8) });
  }

  function toggleSwitch(checked, onChange) {
    const lbl = h("label", { className: "nsw-toggle-cb" });
    const inp = h("input", { type: "checkbox" });
    inp.checked = checked;
    inp.addEventListener("change", () => onChange(inp.checked));
    lbl.appendChild(inp);
    lbl.appendChild(h("span", { className: "slider" }));
    return lbl;
  }

  function selectEl(options, value, onChange, placeholder, disabled) {
    const sel = h("select", { className: "nsw-select" });
    if (disabled) sel.disabled = true;
    if (placeholder) {
      const opt = h("option", { value: "", textContent: placeholder });
      opt.disabled = true;
      if (!value) opt.selected = true;
      sel.appendChild(opt);
    }
    for (const o of options) {
      const opt = h("option", { value: o.value, textContent: o.label });
      if (o.value === value) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => onChange(sel.value));
    return sel;
  }

  /* ── persistent state (survives rebuilds) ────────────── */
  const S = {
    eventsRef: { visible: false, editIndex: -1, targetTable: "", sourceCol: "", targetCol: "", keyType: "Integer", exportName: "", targetCols: [], loading: false },
    casesRef:  { visible: false, editIndex: -1, targetTable: "", sourceCol: "", targetCol: "", keyType: "Integer", exportName: "", targetCols: [], loading: false },
    pendingRefFor: null,
    attrsOpen: false,
    outputOpen: true,
    copied: false,
  };

  function resetRefForm(form) {
    form.visible = false;
    form.editIndex = -1;
    form.targetTable = "";
    form.sourceCol = "";
    form.targetCol = "";
    form.keyType = "Integer";
    form.exportName = "";
    form.targetCols = [];
    form.loading = false;
  }

  /* ── rebuild UI ──────────────────────────────────────── */
  function rebuild() {
    root.innerHTML = "";
    root.appendChild(buildHeader());
    root.appendChild(buildLakehouseCard());
    root.appendChild(buildEventsCard());
    root.appendChild(buildCasesCard());
    root.appendChild(buildAttributesCard());
    root.appendChild(buildOutputCard());
  }

  /* ── header ──────────────────────────────────────────── */
  function buildHeader() {
    return h("div", { className: "nsw-header" },
      h("div", { style: "display:flex;align-items:baseline;gap:8px" },
        h("h2", { textContent: "Normalized Schema Generator" }),
        h("span", { className: "nsw-version", textContent: "v0.3.0" })
      ),
      h("p", { textContent: "Configure your process mining star-schema and generate the dataSource JSON." })
    );
  }

  /* ── Section A: Lakehouse & source type ──────────────── */
  function buildLakehouseCard() {
    const card = h("div", { className: "nsw-card" });
    const lakehouses = model.get("lakehouses") || [];
    const selId = model.get("selected_lakehouse_id") || "";
    const fileType = model.get("data_source_file_type");

    // Lakehouse selector
    const status = model.get("loading_status") || "";
    const lhLoading = !!(status && (status.includes("tables") || status.includes("Reloading")));
    const lhOpts = lakehouses.map(l => ({ value: l.id, label: l.displayName }));
    const lhRow = h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label", textContent: "Lakehouse" }),
      selectEl(lhOpts, selId, (val) => {
        const lh = lakehouses.find(l => l.id === val);
        model.set("selected_lakehouse_id", val);
        model.set("selected_lakehouse_name", lh ? lh.displayName : "");
        model.save_changes();
      }, "Select a lakehouse...", lhLoading)
    );
    if (lhLoading) {
      lhRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: status }));
    }
    card.appendChild(lhRow);

    // Data source type toggle
    const toggle = h("div", { className: "nsw-toggle" });
    const btnDelta = h("button", {
      textContent: "Delta Tables",
      className: fileType === 2 ? "active" : "",
      onClick: () => { model.set("data_source_file_type", 2); model.save_changes(); }
    });
    const btnFiles = h("button", {
      textContent: "Files",
      className: fileType < 2 ? "active" : "",
      onClick: () => { model.set("data_source_file_type", 0); model.save_changes(); }
    });
    toggle.appendChild(btnDelta);
    toggle.appendChild(btnFiles);

    const row2 = h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label", textContent: "Source type" }),
      toggle
    );

    // File format sub-selector (only for Files mode)
    if (fileType < 2) {
      const fmtOpts = [{ value: "0", label: "CSV" }, { value: "1", label: "Parquet" }];
      row2.appendChild(h("span", { className: "nsw-label", textContent: "Format", style: "margin-left:12px" }));
      row2.appendChild(selectEl(fmtOpts, String(fileType), (v) => {
        model.set("data_source_file_type", parseInt(v));
        model.save_changes();
      }));
    }
    card.appendChild(row2);

    return card;
  }

  /* ── Column checklist ────────────────────────────────── */
  function buildColumnList(columns, onChange) {
    if (!columns || columns.length === 0) return h("div", { className: "nsw-empty", textContent: "No columns loaded." });
    const wrap = h("div", { className: "nsw-col-list" });
    columns.forEach((col, i) => {
      wrap.appendChild(h("div", { className: "nsw-col-row" },
        toggleSwitch(col.included, (val) => {
          const updated = JSON.parse(JSON.stringify(columns));
          updated[i].included = val;
          onChange(updated);
        }),
        h("span", { className: "nsw-col-name", textContent: col.name }),
        dtypeBadge(col.dtype)
      ));
    });
    return wrap;
  }

  /* ── Join list ───────────────────────────────────────── */
  function buildJoinList(joins, onEdit, onRemove) {
    if (!joins || joins.length === 0) return h("div", { className: "nsw-empty", textContent: "No references configured." });
    const wrap = h("div", null);
    joins.forEach((j, i) => {
      const targetColNames = (j.target_columns || []).filter(c => c.included).map(c => c.name).join(", ");
      wrap.appendChild(h("div", { className: "nsw-join-item" },
        h("span", { className: "join-type", textContent: j.join_key_type }),
        h("span", { className: "join-desc", textContent: j.source_column + " → " + j.target_table + "." + j.target_column }),
        targetColNames ? h("span", { className: "join-desc", textContent: "cols: " + targetColNames, style: "color:#666" }) : null,
        h("button", { className: "nsw-btn sm", textContent: "Edit", onClick: () => onEdit(i) }),
        h("button", { className: "nsw-btn danger sm", textContent: "Remove", onClick: () => onRemove(i) })
      ));
    });
    return wrap;
  }

  /* ── Add Reference form ──────────────────────────────── */
  function buildRefForm(datasetType) {
    const form = datasetType === "events" ? S.eventsRef : S.casesRef;
    if (!form.visible) return null;

    const tables = model.get("available_tables") || [];
    const existingJoins = model.get(datasetType + "_joins") || [];
    const currentTable = model.get(datasetType + "_table_name") || "";
    const currentCols = model.get(datasetType + "_columns") || [];
    const usedTargets = existingJoins.map(j => j.target_table);

    // Exclude current table and already-referenced tables (but allow the edited one)
    const editingTarget = form.editIndex >= 0 && existingJoins[form.editIndex] ? existingJoins[form.editIndex].target_table : null;
    let availTargets = tables.filter(t => t.name !== currentTable && (!usedTargets.includes(t.name) || t.name === editingTarget));
    const targetOpts = availTargets.map(t => ({ value: t.name, label: t.name }));
    const isEditing = form.editIndex >= 0;

    const wrap = h("div", { className: "nsw-ref-form" });
    const tgtRow = h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label", textContent: "Target table" }),
      isEditing
        ? h("span", { className: "nsw-col-name", textContent: form.targetTable, style: "padding:5px 0" })
        : selectEl(targetOpts, form.targetTable, (val) => {
            form.targetTable = val;
            form.targetCol = "";
            form.sourceCol = "";
            form.targetCols = [];
            form.loading = true;
            S.pendingRefFor = datasetType;
            model.set("fetch_columns_request", Date.now() + ":" + val);
            model.save_changes();
            rebuild();
          }, "Select target table...")
    );
    if (form.loading) {
      tgtRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: "Loading columns..." }));
    }
    wrap.appendChild(tgtRow);

    if (!form.loading && form.targetCols.length > 0) {
      // FK + PK side-by-side
      const srcColOpts = currentCols.map(c => ({ value: c.name, label: c.name + " (" + c.dtype + ")" }));
      const tgtColOpts = form.targetCols.map(c => ({ value: c.name, label: c.name + " (" + c.dtype + ")" }));
      wrap.appendChild(h("div", { className: "nsw-pair-row" },
        h("div", { className: "nsw-pair" },
          h("span", { className: "nsw-label sm", textContent: "FK column" }),
          selectEl(srcColOpts, form.sourceCol, (v) => { form.sourceCol = v; rebuild(); }, "Source FK...")
        ),
        h("div", { className: "nsw-pair" },
          h("span", { className: "nsw-label sm", textContent: "PK column" }),
          selectEl(tgtColOpts, form.targetCol, (v) => { form.targetCol = v; rebuild(); }, "Target PK...")
        )
      ));

      // Key type + Export name side-by-side
      const keyOpts = [{ value: "Integer", label: "Integer" }, { value: "String", label: "String" }];
      const expInp = h("input", {
        type: "text",
        className: "nsw-input",
        placeholder: "Optional export name",
        style: "min-width:140px;flex:1",
      });
      expInp.value = form.exportName;
      expInp.addEventListener("input", (e) => { form.exportName = e.target.value; });
      wrap.appendChild(h("div", { className: "nsw-pair-row" },
        h("div", { className: "nsw-pair" },
          h("span", { className: "nsw-label sm", textContent: "Key type" }),
          selectEl(keyOpts, form.keyType, (v) => { form.keyType = v; rebuild(); })
        ),
        h("div", { className: "nsw-pair" },
          h("span", { className: "nsw-label sm", textContent: "Export name" }),
          expInp
        )
      ));

      // Target columns to include
      wrap.appendChild(h("div", { className: "nsw-joins-title", textContent: "Columns to include from target:" }));
      const colList = h("div", { className: "nsw-col-list" });
      form.targetCols.forEach((col, i) => {
        colList.appendChild(h("div", { className: "nsw-col-row" },
          toggleSwitch(col.included, (val) => { form.targetCols[i].included = val; rebuild(); }),
          h("span", { className: "nsw-col-name", textContent: col.name }),
          dtypeBadge(col.dtype)
        ));
      });
      wrap.appendChild(colList);
    }

    // Buttons
    const canConfirm = form.targetTable && form.sourceCol && form.targetCol && !form.loading;
    const actions = h("div", { className: "form-actions" },
      h("button", {
        className: "nsw-btn primary",
        textContent: isEditing ? "Save" : "Confirm",
        disabled: canConfirm ? undefined : "disabled",
        onClick: () => {
          if (!canConfirm) return;
          const joins = JSON.parse(JSON.stringify(model.get(datasetType + "_joins") || []));
          const entry = {
            target_table: form.targetTable,
            source_column: form.sourceCol,
            target_column: form.targetCol,
            join_key_type: form.keyType,
            export_name: form.exportName || "",
            target_columns: form.targetCols.map(c => ({ name: c.name, dtype: c.dtype, included: c.included })),
          };
          if (isEditing && form.editIndex < joins.length) {
            joins[form.editIndex] = entry;
          } else {
            joins.push(entry);
          }
          model.set(datasetType + "_joins", joins);
          model.save_changes();
          resetRefForm(form);
          rebuild();
        }
      }),
      h("button", {
        className: "nsw-btn",
        textContent: "Cancel",
        onClick: () => { resetRefForm(form); rebuild(); }
      })
    );
    wrap.appendChild(actions);
    return wrap;
  }

  /* ── Section B: Events table ─────────────────────────── */
  function buildEventsCard() {
    const card = h("div", { className: "nsw-card events" });
    card.appendChild(h("div", { className: "nsw-card-title" },
      h("span", { textContent: "Events Table" }),
      h("span", { className: "badge", textContent: "Required" })
    ));

    const tables = model.get("available_tables") || [];
    const selTable = model.get("events_table_name") || "";
    const columns = model.get("events_columns") || [];
    const joins = model.get("events_joins") || [];
    const selLH = model.get("selected_lakehouse_id") || "";

    // Table selector
    const loadingStatus = model.get("loading_status") || "";
    const isLoadingTables = loadingStatus.includes("tables") || loadingStatus.includes("Reloading");
    const isLoadingCols = selTable && columns.length === 0 && loadingStatus.includes("columns");
    const tOpts = tables.map(t => ({ value: t.name, label: t.name }));
    const tblRow = h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label", textContent: "Table" }),
      selectEl(tOpts, selTable, (val) => {
        model.set("events_table_name", val);
        model.set("events_joins", []);
        model.save_changes();
        resetRefForm(S.eventsRef);
      }, isLoadingTables ? "Loading tables..." : (selLH ? "Select events table..." : "Select a lakehouse first"), !selLH || isLoadingTables || isLoadingCols)
    );
    if (isLoadingTables) {
      tblRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: "" }));
    } else if (isLoadingCols) {
      tblRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: "Loading columns..." }));
    }
    card.appendChild(tblRow);

    // Columns
    if (selTable && columns.length > 0) {
      card.appendChild(h("div", { className: "nsw-joins-title", textContent: "Columns" }));
      card.appendChild(buildColumnList(columns, (updated) => {
        model.set("events_columns", updated);
        model.save_changes();
      }));
    }

    // References
    if (selTable) {
      card.appendChild(h("hr", { className: "nsw-divider" }));
      card.appendChild(h("div", { className: "nsw-joins-title", textContent: "References (Joins)" }));
      card.appendChild(buildJoinList(joins, (idx) => {
        const j = joins[idx];
        S.eventsRef.visible = true;
        S.eventsRef.editIndex = idx;
        S.eventsRef.targetTable = j.target_table;
        S.eventsRef.sourceCol = j.source_column;
        S.eventsRef.targetCol = j.target_column;
        S.eventsRef.keyType = j.join_key_type;
        S.eventsRef.exportName = j.export_name || "";
        S.eventsRef.targetCols = (j.target_columns || []).map(c => ({ name: c.name, dtype: c.dtype, included: c.included }));
        S.eventsRef.loading = false;
        rebuild();
      }, (idx) => {
        const updated = JSON.parse(JSON.stringify(joins));
        updated.splice(idx, 1);
        model.set("events_joins", updated);
        model.save_changes();
      }));

      // Add Reference button / form
      const refForm = buildRefForm("events");
      if (refForm) {
        card.appendChild(refForm);
      } else {
        card.appendChild(h("button", {
          className: "nsw-btn outline",
          textContent: "+ Add Reference",
          style: "margin-top:6px",
          onClick: () => { S.eventsRef.visible = true; rebuild(); }
        }));
      }
    }

    return card;
  }

  /* ── Cases: Events→Cases relationship form ─────────── */
  function buildCasesRelationship() {
    const evtCols = model.get("events_columns") || [];
    const casesCols = model.get("cases_columns") || [];
    const rel = model.get("cases_relationship") || {};
    const evtTable = model.get("events_table_name") || "";

    if (!evtTable) {
      return h("div", { className: "nsw-rel-form" },
        h("div", { className: "rel-title", textContent: "Link to Events Table" }),
        h("div", { className: "nsw-empty", textContent: "Select an events table first to configure the relationship." })
      );
    }

    const wrap = h("div", { className: "nsw-rel-form" });
    wrap.appendChild(h("div", { className: "rel-title", textContent: "Link to Events Table (required)" }));

    // FK + PK side-by-side
    const fkOpts = evtCols.map(c => ({ value: c.name, label: c.name + " (" + c.dtype + ")" }));
    const pkOpts = casesCols.map(c => ({ value: c.name, label: c.name + " (" + c.dtype + ")" }));
    wrap.appendChild(h("div", { className: "nsw-pair-row" },
      h("div", { className: "nsw-pair" },
        h("span", { className: "nsw-label sm", textContent: "Events FK" }),
        selectEl(fkOpts, rel.fk_column || "", (v) => {
          const updated = Object.assign({}, rel, { fk_column: v });
          model.set("cases_relationship", updated);
          model.save_changes();
        }, "Column on " + evtTable + "...")
      ),
      h("div", { className: "nsw-pair" },
        h("span", { className: "nsw-label sm", textContent: "Cases PK" }),
        selectEl(pkOpts, rel.pk_column || "", (v) => {
          const updated = Object.assign({}, rel, { pk_column: v });
          model.set("cases_relationship", updated);
          model.save_changes();
        }, "Column on cases...")
      )
    ));

    // Key type + status on one row
    const keyOpts = [{ value: "Integer", label: "Integer" }, { value: "String", label: "String" }];
    const configured = rel.fk_column && rel.pk_column;
    wrap.appendChild(h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label sm", textContent: "Key type" }),
      selectEl(keyOpts, rel.join_key_type || "Integer", (v) => {
        const updated = Object.assign({}, rel, { join_key_type: v });
        model.set("cases_relationship", updated);
        model.save_changes();
      }),
      h("span", { className: "nsw-rel-status " + (configured ? "configured" : "missing"),
        textContent: configured
          ? evtTable + "." + rel.fk_column + " → cases." + rel.pk_column
          : "Select FK and PK columns"
      })
    ));

    return wrap;
  }

  /* ── Section C: Cases table ──────────────────────────── */
  function buildCasesCard() {
    const enabled = model.get("cases_enabled");

    if (!enabled) {
      return h("div", { className: "nsw-cases-placeholder" },
        h("button", {
          className: "nsw-btn outline",
          textContent: "+ Add Cases Table (optional)",
          onClick: () => { model.set("cases_enabled", true); model.save_changes(); }
        })
      );
    }

    const card = h("div", { className: "nsw-card cases" });
    card.appendChild(h("div", { className: "nsw-card-title" },
      h("span", { textContent: "Cases Table" }),
      h("span", { className: "badge", textContent: "Optional" }),
      h("span", { style: "flex:1" }),
      h("button", {
        className: "nsw-btn danger",
        textContent: "Remove Cases Table",
        onClick: () => {
          model.set("cases_enabled", false);
          model.set("cases_table_name", "");
          model.set("cases_columns", []);
          model.set("cases_joins", []);
          model.set("cases_relationship", {});
          model.save_changes();
          resetRefForm(S.casesRef);
        }
      })
    ));

    const tables = model.get("available_tables") || [];
    const selTable = model.get("cases_table_name") || "";
    const columns = model.get("cases_columns") || [];
    const joins = model.get("cases_joins") || [];
    const selLH = model.get("selected_lakehouse_id") || "";

    const casesLoadingStatus = model.get("loading_status") || "";
    const casesLoadingTables = casesLoadingStatus.includes("tables") || casesLoadingStatus.includes("Reloading");
    const casesLoadingCols = selTable && columns.length === 0 && casesLoadingStatus.includes("columns");
    const tOpts = tables.map(t => ({ value: t.name, label: t.name }));
    const casesTblRow = h("div", { className: "nsw-row" },
      h("span", { className: "nsw-label", textContent: "Table" }),
      selectEl(tOpts, selTable, (val) => {
        model.set("cases_table_name", val);
        model.set("cases_joins", []);
        model.set("cases_relationship", {});
        model.save_changes();
        resetRefForm(S.casesRef);
      }, casesLoadingTables ? "Loading tables..." : (selLH ? "Select cases table..." : "Select a lakehouse first"), !selLH || casesLoadingTables || casesLoadingCols)
    );
    if (casesLoadingTables) {
      casesTblRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: "" }));
    } else if (casesLoadingCols) {
      casesTblRow.appendChild(h("span", { className: "nsw-loading-inline", textContent: "Loading columns..." }));
    }
    card.appendChild(casesTblRow);

    // Events→Cases relationship (shown as soon as cases table is selected)
    if (selTable && columns.length > 0) {
      card.appendChild(buildCasesRelationship());
    }

    if (selTable && columns.length > 0) {
      card.appendChild(h("div", { className: "nsw-joins-title", textContent: "Columns" }));
      card.appendChild(buildColumnList(columns, (updated) => {
        model.set("cases_columns", updated);
        model.save_changes();
      }));
    }

    if (selTable) {
      card.appendChild(h("hr", { className: "nsw-divider" }));
      card.appendChild(h("div", { className: "nsw-joins-title", textContent: "References (Join lookups only)" }));
      card.appendChild(buildJoinList(joins, (idx) => {
        const j = joins[idx];
        S.casesRef.visible = true;
        S.casesRef.editIndex = idx;
        S.casesRef.targetTable = j.target_table;
        S.casesRef.sourceCol = j.source_column;
        S.casesRef.targetCol = j.target_column;
        S.casesRef.keyType = j.join_key_type;
        S.casesRef.exportName = j.export_name || "";
        S.casesRef.targetCols = (j.target_columns || []).map(c => ({ name: c.name, dtype: c.dtype, included: c.included }));
        S.casesRef.loading = false;
        rebuild();
      }, (idx) => {
        const updated = JSON.parse(JSON.stringify(joins));
        updated.splice(idx, 1);
        model.set("cases_joins", updated);
        model.save_changes();
      }));

      const refForm = buildRefForm("cases");
      if (refForm) {
        card.appendChild(refForm);
      } else {
        card.appendChild(h("button", {
          className: "nsw-btn outline",
          textContent: "+ Add Reference",
          style: "margin-top:6px",
          onClick: () => { S.casesRef.visible = true; rebuild(); }
        }));
      }
    }

    return card;
  }

  /* ── SVG icons for attribute types (16x16 viewBox) ──── */
  const ICONS = {
    caseid: '<svg viewBox="0 0 16 16"><path d="M10 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h1a2 2 0 0 1 2 2v.27c.6.34 1 .99 1 1.73a2 2 0 0 1-4 0c0-.74.4-1.39 1-1.73V9a1 1 0 0 0-1-1H6a1 1 0 0 0-1 1v.27c.6.34 1 .99 1 1.73a2 2 0 0 1-4 0c0-.74.4-1.39 1-1.73V9a2 2 0 0 1 2-2h1V5.73C5.4 5.39 5 4.74 5 4a2 2 0 0 1 2-2"/><circle cx="10" cy="4" r="1"/><circle cx="6" cy="11" r="1"/><circle cx="14" cy="11" r="1"/>',
    activity: '<svg viewBox="0 0 16 16"><polygon points="5,2 14,8 5,14"/>',
    start: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5" fill="none"/><path d="M8 4v4l3 1.5"/>',
    end: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5" fill="none"/><path d="M5.5 8.5l2 2 3.5-4"/>',
    resource: '<svg viewBox="0 0 16 16"><circle cx="8" cy="5" r="2.5" fill="none"/><path d="M3 14c0-2.8 2.2-5 5-5s5 2.2 5 5"/>',
    evtattr: '<svg viewBox="0 0 16 16"><path d="M2 4h12M2 8h12M2 12h8"/>',
    caseattr: '<svg viewBox="0 0 16 16"><path d="M2 3h5l1.5 2H14v8H2z"/>',
    fin_evt: '<svg viewBox="0 0 16 16"><path d="M2 4h7M2 8h7M2 12h5"/><text x="13" y="10" text-anchor="middle" font-size="7" font-weight="700" fill="currentColor" stroke="none">$</text>',
    fin_case: '<svg viewBox="0 0 16 16"><path d="M2 3h5l1.5 2H11v5H2z"/><text x="14" y="12" text-anchor="middle" font-size="7" font-weight="700" fill="currentColor" stroke="none">$</text>',
    fin_res: '<svg viewBox="0 0 16 16"><circle cx="6" cy="5" r="2.5" fill="none"/><path d="M1 14c0-2.8 2.2-5 5-5s5 2.2 5 5"/><text x="14" y="7" text-anchor="middle" font-size="7" font-weight="700" fill="currentColor" stroke="none">$</text>',
  };

  /* Attribute type definitions: key, icon, tooltip, ImportType, Level, FinanceImportType */
  const ATTR_TYPES = [
    { key: "caseid",   icon: ICONS.caseid,   tip: "Case ID",              it: "Case",     lv: "Event", fi: "None" },
    { key: "activity", icon: ICONS.activity,  tip: "Activity",             it: "Activity",  lv: "Event", fi: "None" },
    { key: "start",    icon: ICONS.start,     tip: "Start timestamp",      it: "Start",     lv: "Event", fi: "None" },
    { key: "end",      icon: ICONS.end,       tip: "End timestamp",        it: "End",       lv: "Event", fi: "None" },
    { key: "resource", icon: ICONS.resource,  tip: "Resource",             it: "Resource",  lv: "Event", fi: "None" },
    { key: "evtattr",  icon: ICONS.evtattr,   tip: "Event-level attribute",it: "Other",     lv: "Event", fi: "None" },
    { key: "caseattr", icon: ICONS.caseattr,  tip: "Case-level attribute", it: "Other",     lv: "Case",  fi: "None" },
    { key: "fin_evt",  icon: ICONS.fin_evt,   tip: "Finance per event",    it: "Other",     lv: "Event", fi: "PerEvent" },
    { key: "fin_case", icon: ICONS.fin_case,  tip: "Finance per case",     it: "Other",     lv: "Case",  fi: "PerCase" },
    { key: "fin_res",  icon: ICONS.fin_res,   tip: "Finance per resource", it: "Other",     lv: "Event", fi: "PerResource" },
  ];

  function attrTypeKey(a) {
    for (const t of ATTR_TYPES) {
      if (t.it === a.ImportType && t.lv === a.Level && t.fi === a.FinanceImportType) return t.key;
    }
    return "evtattr";
  }

  const DTYPES = [
    { key: "String",  label: "STR",  cls: "t-str" },
    { key: "Integer", label: "INT",  cls: "t-int" },
    { key: "Float",   label: "FLT",  cls: "t-flt" },
    { key: "Date",    label: "DATE", cls: "t-date" },
    { key: "Boolean", label: "BOOL", cls: "t-bool" },
  ];

  /* ── Section D: Attribute Mapping ────────────────────── */
  function buildAttributesCard() {
    const attrs = model.get("attributes_config") || [];
    if (attrs.length === 0) return h("div", null);

    const card = h("div", { className: "nsw-card attrs" });

    const toggleBtn = h("button", { className: "nsw-collapse-btn", onClick: () => { S.attrsOpen = !S.attrsOpen; rebuild(); } },
      h("span", { className: "arrow " + (S.attrsOpen ? "open" : ""), textContent: "▶" }),
      h("span", { textContent: "Attribute Mapping" }),
      h("span", { style: "font-weight:400;color:var(--f-text3);font-size:12px;margin-left:4px", textContent: "(" + attrs.length + ")" })
    );
    card.appendChild(toggleBtn);

    if (!S.attrsOpen) return card;

    function updAttr(i, fields) {
      const updated = JSON.parse(JSON.stringify(attrs));
      Object.assign(updated[i], fields);
      model.set("attributes_config", updated);
      model.save_changes();
    }

    const list = h("div", { className: "nsw-attr-list" });
    attrs.forEach((attr, i) => {
      const curType = attrTypeKey(attr);
      const isCaseLevel = attr.Level === "Case";

      // Attribute type icon group
      const typeGroup = h("div", { className: "nsw-icon-group" });
      for (const t of ATTR_TYPES) {
        const btn = h("button", {
          className: "nsw-icon-btn" + (curType === t.key ? " active" : ""),
          title: t.tip,
          innerHTML: t.icon,
          onClick: () => updAttr(i, { ImportType: t.it, Level: t.lv, FinanceImportType: t.fi })
        });
        typeGroup.appendChild(btn);
      }

      // First/Last toggle (always visible, disabled when not case-level)
      const curFL = attr.CaseLevelAttributeHandling || "First";
      const flGroup = h("div", { className: "nsw-fl-group" + (isCaseLevel ? "" : " disabled") });
      for (const v of ["First", "Last"]) {
        flGroup.appendChild(h("button", {
          className: "nsw-fl-btn" + (isCaseLevel && curFL === v ? " active" : ""),
          textContent: v,
          onClick: isCaseLevel ? () => updAttr(i, { CaseLevelAttributeHandling: v }) : null
        }));
      }

      // Data type pills
      const dtGroup = h("div", { className: "nsw-dtype-group" });
      const curDt = attr.SourceDataType || "String";
      for (const d of DTYPES) {
        dtGroup.appendChild(h("button", {
          className: "nsw-dtype-btn " + d.cls + (curDt === d.key ? " active" : ""),
          textContent: d.label,
          onClick: () => updAttr(i, { SourceDataType: d.key })
        }));
      }

      const row = h("div", { className: "nsw-attr-row" },
        h("span", { className: "nsw-attr-name", textContent: attr.Name }),
        typeGroup,
        flGroup,
        dtGroup
      );
      list.appendChild(row);
    });

    card.appendChild(list);
    return card;
  }

  /* ── Section E: JSON output ──────────────────────────── */
  function buildOutputCard() {
    const card = h("div", { className: "nsw-card output" });
    const json = model.get("config_json") || "";

    const toggleBtn = h("button", { className: "nsw-collapse-btn", onClick: () => { S.outputOpen = !S.outputOpen; rebuild(); } },
      h("span", { className: "arrow " + (S.outputOpen ? "open" : ""), textContent: "▶" }),
      h("span", { textContent: "Generated JSON Config" })
    );
    card.appendChild(toggleBtn);

    if (S.outputOpen) {
      if (!json) {
        card.appendChild(h("div", { className: "nsw-empty", textContent: "Select a lakehouse and events table to generate the config." }));
      } else {
        const wrap = h("div", { className: "nsw-json-wrap" });
        wrap.appendChild(h("div", { className: "nsw-json", textContent: json }));
        const btnGroup = h("div", { className: "nsw-json-actions" });
        if (S.copied) {
          btnGroup.appendChild(h("span", { className: "nsw-copied", textContent: "Copied!" }));
        } else {
          btnGroup.appendChild(h("button", {
            className: "nsw-btn outline sm",
            textContent: "Copy",
            onClick: () => {
              navigator.clipboard.writeText(json).then(() => {
                S.copied = true;
                rebuild();
                setTimeout(() => { S.copied = false; rebuild(); }, 1500);
              });
            }
          }));
        }
        btnGroup.appendChild(h("button", {
          className: "nsw-btn outline sm",
          textContent: "Save",
          onClick: () => {
            const blob = new Blob([json], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "normalized_config.json";
            a.click();
            URL.revokeObjectURL(url);
          }
        }));
        wrap.appendChild(btnGroup);
        card.appendChild(wrap);
      }
    }

    // Error messages
    const err = model.get("error_message") || "";
    if (err) card.appendChild(h("div", { className: "nsw-error", textContent: err }));

    return card;
  }

  /* ── model change listeners ──────────────────────────── */
  const watchedTraits = [
    "lakehouses", "selected_lakehouse_id", "data_source_file_type",
    "available_tables", "events_table_name", "events_columns", "events_joins",
    "cases_enabled", "cases_table_name", "cases_columns", "cases_joins", "cases_relationship",
    "attributes_config", "config_json", "loading_status", "error_message"
  ];
  for (const t of watchedTraits) {
    model.on("change:" + t, rebuild);
  }

  // Handle fetch_columns_response
  model.on("change:fetch_columns_response", () => {
    const resp = model.get("fetch_columns_response") || [];
    const target = S.pendingRefFor;
    if (target) {
      const form = target === "events" ? S.eventsRef : S.casesRef;
      form.targetCols = resp.map(c => ({ name: c.name, dtype: c.dtype, included: true }));
      form.loading = false;
      S.pendingRefFor = null;
    }
    rebuild();
  });

  rebuild();
}
