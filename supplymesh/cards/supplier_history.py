"""Small read-only supplier order history cards."""

from decimal import Decimal
from typing import Any

from supplymesh.cards.renderer import CardEnvelope, render_card


def history_filters(supplier_ids: tuple[str, ...]) -> CardEnvelope:
    return render_card(
        "form",
        {
            "title": "Filter supplier history",
            "fields": [
                {
                    "name": "manufacturer_id",
                    "label": "Manufacturer ID (blank for all)",
                    "kind": "text",
                },
                {
                    "name": "supplier_id",
                    "label": "Supplier (blank for all)",
                    "kind": "select",
                    "options": [{"value": "", "label": "All suppliers"}]
                    + [{"value": item, "label": item} for item in supplier_ids],
                },
            ],
            "submit_cta": {"label": "Show history", "selection": {"action": "supplier_history"}},
        },
    )


def supplier_history_card(page: dict[str, Any], filters: dict[str, Any]) -> CardEnvelope:
    rows = []
    for item in page["items"]:
        lines = item["lines"]
        parts = "; ".join(
            f"{line['component_sku']}: {format(Decimal(line['quantity']).normalize(), 'f')} "
            f"{line['unit']} at {Decimal(line['unit_price']):.2f}, due {line['requested_date']}"
            for line in lines[:20]
        )
        if len(lines) > 20:
            parts += f"; +{len(lines) - 20} more lines in the stored order"
        rows.append(
            {
                "label": f"{item['po_number']} / v{item['po_version']}",
                "value": (
                    f"{item['manufacturer_name']} ({item['manufacturer_id']}) "
                    f"→ {item['supplier_id']}. "
                    f"{'Purchase order' if item['document_type'] == '850' else 'Change order'}; "
                    f"{item['status']}; {item['created_at'][:19].replace('T', ' ')} UTC. "
                    f"{item['currency']} {Decimal(item['total']):.2f}. Items: {parts}"
                ),
            }
        )
    if not rows:
        rows = [{"label": "Orders", "value": "No supplier history matches these filters."}]
    ctas = []
    if page["next_offset"] is not None:
        ctas.append(
            {
                "label": "Show more",
                "selection": {
                    "action": "supplier_history",
                    "values": {**filters, "offset": page["next_offset"]},
                },
            }
        )
    ctas.extend(
        [
            {"label": "Filter history", "selection": {"action": "supplier_history_filters"}},
            {"label": "Done", "selection": {"action": "dismiss_card"}},
        ]
    )
    return render_card("detail", {"title": "Supplier order history", "rows": rows, "ctas": ctas})
