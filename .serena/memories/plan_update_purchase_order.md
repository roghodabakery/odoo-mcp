# Plan: Add `update_purchase_order` Tool

## Goal
Add an MCP tool to `tools/purchase.py` that allows an AI agent to update a **draft** purchase order in Odoo — updating header fields and/or replacing order lines.

---

## Only File to Modify
`tools/purchase.py` — no other files need changes.

---

## Tool Specification

### Name
`update_purchase_order`

### Placement
Inside `register_tools()`, after `create_purchase_order` and before `confirm_order`.

### Signature
```python
async def update_purchase_order(
    po_id: int,
    partner_id: int | None = None,
    date_order: str | None = None,         # YYYY-MM-DD
    lines: List[Dict[str, Any]] | None = None,
    ctx: Context = None
) -> dict:
```

### Docstring intent
Update a draft purchase order. Can update supplier (partner_id), date (date_order),
and/or replace all order lines. Only works on POs in 'draft' state.

---

## Implementation Steps

### Step 1 — Read & Validate State
- Use `models.execute_kw(..., "purchase.order", "read", [[po_id]], {"fields": ["name", "state", "amount_total"]})`
- If PO not found → raise `ValueError("Purchase order {po_id} not found")`
- If state != 'draft' → raise `ValueError("Purchase order {po_id} is in state '{state}' and cannot be modified. Only draft POs can be updated.")`

### Step 2 — Build `vals` dict
- Start with `vals = {}`
- If `partner_id` is not None → `vals["partner_id"] = partner_id`
- If `date_order` is not None → `vals["date_order"] = date_order`

### Step 3 — Handle Line Replacement (if `lines` provided)
- Filter out any line dicts missing `product_id`
- If no valid lines remain after filter → raise `ValueError("No lines with a resolved product_id")`
- Build ORM command list:
  ```python
  order_line_cmds = [(5, 0, 0)]  # delete all existing lines
  for line in lines:
      order_line_cmds.append((0, 0, {
          "product_id": line["product_id"],
          "name": line.get("name") or line.get("description", ""),
          "product_qty": line.get("qty") or line.get("quantity", 1),
          "price_unit": line.get("price_unit") or line.get("unit_price", 0),
          "date_planned": date_order or <existing PO date or today>,
      }))
  vals["order_line"] = order_line_cmds
  ```

### Step 4 — Guard Empty Update
- If `vals` is empty → return current PO data without calling write (no-op)
- Log a ctx.info warning: "No fields to update"

### Step 5 — Execute Write
```python
models.execute_kw(
    settings.odoo_db, uid, settings.odoo_api_key,
    "purchase.order", "write",
    [[po_id], vals]
)
```

### Step 6 — Re-read & Return
- Read updated PO: `name`, `partner_id`, `amount_total`, `state`, `order_line`
- Read line details from `purchase.order.line`
- Return:
  ```python
  {
      "success": True,
      "po_id": po_id,
      "po_name": ...,
      "partner_id": ...,
      "amount_total": ...,
      "state": "draft",
      "order_lines": [...]
  }
  ```

---

## Key Odoo ORM Commands Reference
| Command | Effect |
|---------|--------|
| `(0, 0, {...})` | Create and link new line |
| `(1, id, {...})` | Update existing line by id |
| `(2, id, 0)` | Delete specific line by id |
| `(5, 0, 0)` | Delete ALL linked lines |

Strategy used: **(5,0,0) + (0,0,{}) per line** = full replacement, simplest and most predictable for AI agent.

---

## Error Cases
| Condition | Error |
|-----------|-------|
| PO not found | `ValueError` |
| PO not in draft | `ValueError` with state info |
| Lines provided but none have product_id | `ValueError` |
| vals is empty | Return current state, no write |

---

## Consistency Notes
- Follows exact same `@mcp.tool()` decorator pattern as existing tools
- Uses same `get_models()` utility
- Uses same `settings.*` references
- ctx.info() logging at start and end
- Return dict shape matches `get_purchase_order` output for consistency
