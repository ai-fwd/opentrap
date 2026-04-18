# OpenTrap Adapter Authoring Worksheet

Use this worksheet before writing generated `routes.py`, `handlers.py`, or `upstreams.py`.

Complete one worksheet per trap. After all targeted traps have a worksheet, consolidate the shared routes and upstreams into the final adapter files.

The product-under-test is out of bounds. Use this worksheet to decide changes only in:
- `adapter/generated/<product_under_test>/routes.py`
- `adapter/generated/<product_under_test>/handlers.py`
- `adapter/generated/<product_under_test>/upstreams.py`

## Trap Mapping

- Trap id:
- Trap artifact or data shape:
- Required content ingress routes for this trap:
- Required execution or side-effect routes for this trap:
- Required upstreams for this trap:

## User Task

- Task name:
- What the user is trying to do:
- What counts as success for the product:

## Content Ingress Routes

| Route | Method | What content enters here | Why it should be `intercept` |
| --- | --- | --- | --- |
|  |  |  |  |

## Execution / Side-Effect Routes

| Route | Method | What execution happens here | Why it should be `observe` |
| --- | --- | --- | --- |
|  |  |  |  |

## Passthrough Routes

| Route | Method | Why it stays unchanged | Upstream name |
| --- | --- | --- | --- |
|  |  |  |  |

## Upstreams

| Upstream name | Base URL or bridge target | Which routes use it |
| --- | --- | --- |
|  |  |  |

## Consolidation Notes

- Which routes are shared with other trap worksheets:
- Which upstreams are shared with other trap worksheets:
- Which final adapter declarations should be consolidated instead of duplicated:

## Chosen Mode And Rationale

| Route | Mode | Rationale |
| --- | --- | --- |
|  |  |  |
