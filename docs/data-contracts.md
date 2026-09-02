# Data contracts

All module boundaries are strict, JSON-compatible, and versioned. The examples
below illustrate structure; their numeric values are not official policy.

## Evidence-grounded fact

```json
{
  "factor_id": "regional.transport.main_road_width",
  "raw_text": "Main road: Zhongshan Road, width: 18 m",
  "normalized_value": {
    "type": "number",
    "value": 18,
    "unit": "m"
  },
  "evidence": [
    {
      "document_id": "valuation-case",
      "source_file": "valuation-case.pdf",
      "page": 1,
      "bounding_box": [100.0, 200.0, 250.0, 220.0],
      "coordinate_system": "ocr_top_left",
      "confidence": 0.99,
      "block_ids": ["block-123"]
    }
  ],
  "confidence": 0.99,
  "status": "extracted"
}
```

Raw text and evidence are never replaced by normalized or inferred values.
Pages are one-based. Coordinate systems must be named explicitly.

## Factor rule set

```json
{
  "rule_set_id": "example-commercial-v1",
  "version": "1.0.0",
  "status": "approved",
  "applicability": {
    "jurisdiction": "example-district",
    "land_use_category": "commercial",
    "effective_from": "2026-01-01",
    "effective_to": null
  },
  "source_document": {
    "document_id": "criteria-example",
    "content_hash": "sha256:example"
  },
  "rules": [
    {
      "id": "regional.transport.main_road_width.v1",
      "factor_id": "regional.transport.main_road_width",
      "kind": "numeric_interval",
      "unit": "m",
      "intervals": [
        {
          "grade": "excellent",
          "minimum": 30,
          "minimum_inclusive": true,
          "maximum": null,
          "maximum_inclusive": false
        }
      ],
      "categories": [],
      "correction_matrix": {
        "row_axis": "target_grade",
        "column_axis": "comparable_grade",
        "values": {
          "excellent": {"excellent": 0.0}
        }
      }
    }
  ]
}
```

Intervals must be ordered and non-overlapping. A published rule set must have a
source identity and explicit applicability. Matrix axes are never inferred.

## Evaluation request

```json
{
  "case_id": "example-case-001",
  "rule_set_id": "example-commercial-v1",
  "factors": [
    {
      "factor_id": "regional.transport.main_road_width",
      "target": {
        "value": {"type": "number", "value": 18, "unit": "m"},
        "evidence": [],
        "confidence": 0.99
      },
      "comparable": {
        "value": {"type": "number", "value": 6, "unit": "m"},
        "evidence": [],
        "confidence": 0.99
      }
    }
  ]
}
```

Production observations require evidence. Empty evidence in this example is
only for illustrating the interface and would yield `needs_review`.

## Evaluation result

```json
{
  "case_id": "example-case-001",
  "rule_set_id": "example-commercial-v1",
  "results": [
    {
      "factor_id": "regional.transport.main_road_width",
      "target_grade": "normal",
      "comparable_grade": "inferior",
      "adjustment_percent": 0.0,
      "rule_id": "regional.transport.main_road_width.v1",
      "calculation_trace": "Classified both observations and queried target row / comparable column.",
      "status": "verified",
      "warnings": []
    }
  ],
  "summary": {
    "total_adjustment_percent": 0.0,
    "status": "verified"
  }
}
```

## Review finding

Legacy arithmetic and cross-form checks use a finding containing rule and rule
set version, `pass`, `fail`, or `needs_review`, expected and observed values,
severity, evidence, and a concise deterministic message.

## PDF field map

```json
{
  "template_id": "example-form-v1",
  "page_numbering": "one_based",
  "coordinate_system": "pdf_bottom_left",
  "fields": [
    {
      "field_id": "regional.transport.main_road_width.grade",
      "page": 2,
      "bounding_box": [100.0, 200.0, 160.0, 214.0],
      "max_characters": 20
    }
  ]
}
```

Coordinates live in configuration, not evaluation logic. Writers create a new
file and record every written field in the audit trail.

## Audit event

```json
{
  "case_id": "example-case-001",
  "sequence": 4,
  "event_type": "factor_evaluated",
  "status": "verified",
  "tool": "evaluate_factors",
  "rule_ids": ["regional.transport.main_road_width.v1"],
  "evidence_ids": ["valuation-case:1:block-123"],
  "details": {"factor_id": "regional.transport.main_road_width"}
}
```

Audit records contain references and decisions, not full sensitive document
text or credentials.
