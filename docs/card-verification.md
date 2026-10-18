# ASI:One card verification

Card builders follow the published [predefined schemas](https://docs.agentverse.ai/documentation/advanced-usages/predefined-card-schemas)
and [element tree primitives](https://docs.agentverse.ai/documentation/advanced-usages/element-tree-primitives).
Forms use `name`, `kind`, and `submit_cta.selection`; dates use text inputs labeled YYYY-MM-DD.
The live playground rejects `subtitle` on forms. Form payloads use only `title`, `fields`,
and `submit_cta`; explanatory text belongs in the accompanying chat message.
Review cards use summary rows and approval/rejection CTAs. Exception cards use an element tree.
Informational detail cards set `is_terminal: "true"` in metadata.
Detail payloads require a nonempty `ctas` array. Informational cards use a Done button whose
`dismiss_card` reply is acknowledged without starting or modifying a workflow.

## Manual rendering check

1. Sign into the [ASI:One card playground](https://asi1.ai/developer/card-playground).
2. Open [sample payloads](samples/asi-cards.json). For each entry, choose its `card_kind`
   and paste its `card_payload` object into the playground.
3. Check form labels and options, carousel navigation, review buttons, detail rows, and
   exception buttons. These are synthetic examples; their targets are not live workflow IDs.
4. With the deployed agents, submit the real demand form and review the inventory quantities.
   Confirm the assessment remains visible until Continue to sourcing is clicked. Then choose a
   plan and approve its review.
   Confirm the separate PO approval appears before any order is sent. Check the Supplier Agent's
   quotation and capacity form separately.

The playground redirected to login during local verification. Rendering, live delivery, and
planner routing still require this manual check; local tests do not establish platform acceptance.

## Direct form replies

ASI:One merges form inputs into the CTA selection at the top level. For example:

```json
{"action":"submit_demand","product_sku":"MOTO-URBANX-2026","quantity":75,"required_by":"2026-12-01","priority":"normal"}
```

A counterproposal keeps the target and version supplied by its actual card:

```json
{"action":"review_counterproposal","target_id":"actual-po-id","target_version":2,"quantity":50,"maximum_price":"820","required_date":"2026-12-01","note":"Please confirm"}
```

The parser also accepts existing nested `values` submissions. It rejects mixed flat/nested
inputs and unknown fields, and omits empty optional form inputs. Approval actions still use
the emitted target and version. Natural-language planner extraction is not implemented.

Captured live ASI:One submissions also include a leading `@agent1...` recipient and a boolean
`approved` field. The parser accepts one valid address mention and removes the UI confirmation
flag. The flag does not select an action or authorize purchase orders. Explicit approval actions
retain the coordinator's workflow-state, target, and version checks; an approval action with
`approved: false` is rejected.

## Redisplay a completed workflow

In the original Orchestrator chat, send a structured selection with the saved workflow UUID:

```json
{"action":"show_completion","target_id":"saved-workflow-uuid"}
```

This reads the completed workflow and resends one completion card with a compact per-agent
activity summary. Totals and feasibility also appear in the accompanying text. Full audit rows
remain in PostgreSQL. It does not
submit orders or change workflow state. Requests from a different conversation or for unfinished
workflows are rejected. The rendered Done button only acknowledges the informational card.

For a pending exception in the original conversation, `{"action":"show_exception"}` resends
the exception card without changing the workflow or submitting orders. Its header identifies
the supplier and PO; line discrepancies identify the component SKU using the PO line number.
