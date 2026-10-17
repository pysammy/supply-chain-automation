# Sample X12 documents

These deterministic, synthetic examples use the scoped educational subset implemented by
SupplyMesh. They contain no production supplier data or credentials.

- `purchase-order-850.x12`: one approved purchase-order line.
- `acknowledgment-855.x12`: exact acceptance of that line.
- `change-order-860.x12`: approved price/date counterproposal, PO version 2.
- `change-response-865.x12`: supplier acceptance of the counterproposal.

Validate them with the matching parser in `supplymesh/edi/`. This repository is not a certified
X12 translator or VAN implementation.
