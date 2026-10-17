1. Manufacturing Orchestrator Agent
Input:
The manager’s request through ASI:One:
“We received an order for 75 motorcycles required by December 1.”

What it does:
- Understands the request.
- Extracts the product, quantity and deadline.
- Asks the user to confirm the information.
- Sends work to the appropriate specialist agents.
- Tracks the current workflow state.
- Shows recommendations and approval options to the human.
- Continues the workflow based on the human’s decision.
Output:
- ASI:One Interactive Cards.
- Requests to other agents.
- Progress updates.
- Final manufacturing and procurement summary.
The Orchestrator does not calculate inventory or generate purchase orders itself. It coordinates the agents that do those tasks.

2. Inventory and BOM Agent
Input:
Product: UrbanX Motorcycle
Quantity: 75
Required date: December 1
Priority: Urgent

What it does:
- Reads the motorcycle’s BOM.
- Calculates how many of each part are required.
- Checks current inventory.
- Protects parts already reserved for existing orders.
- Considers safety stock and confirmed incoming parts.
- Temporarily reserves available parts for the new order.
- Calculates the remaining shortages.
Output:
An InventoryShortageReport, for example:
Engines required: 75
Available after existing reservations: 50
Shortage: 25

Battery units required: 75
Available after existing reservations: 75
Shortage: 0

If there are no shortages, the Orchestrator can complete the planning workflow without purchasing anything.

3. Sourcing and Optimization Agent
Input:
The shortage report from the Inventory Agent:
25 engines needed by December 1
40 brake kits needed by December 1

What it does:
- Finds suppliers that provide the missing parts.
- Sends RFQs to multiple supplier REST APIs.
- Collects prices, quantities, capacity and delivery dates.
- Handles supplier timeouts or invalid quotations.
- Uses OR-Tools to compare possible allocations.
- Creates different procurement plans.
Output:
A ProcurementPlansReady response containing options such as:
- Cheapest plan
- Fastest plan
- Balanced plan
- Risk-aware plan
- Split-supplier plan
The Orchestrator shows these plans to the human for approval.

4. Procurement Execution Agent
Input:
The plan selected and approved by the human:
Buy 25 engines from Supplier Alpha
Buy 25 brake kits from Supplier Beta
Buy 15 brake kits from Supplier Gamma

What it does:
- Confirms that the quotations are still valid.
- Confirms supplier capacity and approved totals.
- Generates X12 850 purchase orders.
- Sends the POs to supplier REST endpoints.
- Prevents duplicate orders using idempotency keys.
- Tracks the supplier’s processing status.
- Retrieves the X12 855 acknowledgment.
- Sends the PO and acknowledgment to the Acknowledgment Agent.
If a counterproposal is approved, it also:
- Generates an X12 860 change request.
- Sends it to the supplier.
- Retrieves the supplier’s X12 865 response.
Output:
- Submitted purchase-order details.
- Supplier transaction IDs.
- X12 855 acknowledgments.
- X12 865 change responses.
- PO and change-order status updates.

5. Acknowledgment and Exception Agent
Input:
- The original X12 850 purchase order.
- The supplier’s X12 855 acknowledgment.
For a counterproposal:
- The X12 860 change request.
- The supplier’s X12 865 response.
What it does:
- Parses both documents.
- Matches every acknowledgment line with the corresponding PO line.
- Compares quantity, price and delivery date.
- Identifies missing, rejected or changed items.
- Calculates the additional cost and production delay.
- Determines the severity of each discrepancy.
- Generates valid response options.
Output when everything matches:
Status: MATCHED
All quantities, prices and delivery dates were accepted.

Output when there is a conflict:
Status: CONFLICT

Engine price:
PO: $820
Acknowledgment: $850

Delivery date:
PO: November 20
Acknowledgment: December 5

Impact:
$750 additional cost
Possible 4-day manufacturing delay

The Orchestrator then asks the human to:
- Accept the changes.
- Send a counterproposal.
- Switch suppliers.
- Split or re-source the order.
- Cancel the order.

6. Sentry Reliability Agent
Input:
- Sentry alerts and webhooks.
- Errors, logs and traces.
- Slow-operation information.
- Failed agent tool calls.
- Diagnostic questions from the Orchestrator or user.
Example:
“Why is workflow WF-742 taking so long?”

What it does:
- Queries Sentry using MCP or the Sentry API.
- Finds relevant traces, errors and logs.
- Uses the workflow ID to connect events across agents.
- Identifies the likely root cause.
- Determines whether a safe automatic recovery is available.
- Executes only approved low-risk recovery actions.
Safe automatic actions include:
- Retrying an idempotent supplier request.
- Marking a supplier as temporarily unavailable.
- Opening a supplier circuit breaker.
- Continuing with quotations from the remaining suppliers.
- Pausing a stuck workflow.
- Notifying the Orchestrator.
It cannot automatically send a PO, accept a price increase or change procurement commitments.
Output:
An IncidentDiagnosis, for example:
Problem:
Supplier Beta's RFQ endpoint timed out three times.

Impact:
The sourcing workflow was delayed by 12 seconds.

Automatic action:
Supplier Beta was marked unavailable for 10 minutes.
Sourcing continued with Alpha and Gamma.

Human approval required:
No

It also sends the Orchestrator:
- Diagnosis evidence.
- Recovery action taken.
- Remaining risk.
- Whether human intervention is required.
Complete agent flow
User in ASI:One
        ↓
Orchestrator Agent
        ↓
Inventory and BOM Agent
        ↓
Sourcing and Optimization Agent
        ↓
Human approves a plan
        ↓
Procurement Execution Agent
        ↓
Supplier REST APIs
        ↓
Acknowledgment and Exception Agent
        ↓
Human decision if conflicts exist
        ↓
Final confirmation

Sentry Reliability Agent monitors all stages
and intervenes safely when failures occur.