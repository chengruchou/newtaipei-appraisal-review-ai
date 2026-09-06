# Competition requirements

## Official problem

The competition topic is **AI-Assisted Real Estate Valuation Case Review**.
Reviewers inspect valuation forms prepared by real estate appraisers and verify
that the evaluation-factor criteria applicable to the case were applied
correctly.

## Manual review checks

The workflow spans multiple forms and includes:

- regional factors such as settlements, markets, transportation, public
  facilities, and undesirable facilities;
- individual parcel factors such as area, shape, road conditions, and access;
- source facts, measurements, distances, and units;
- grade classification such as excellent, slightly superior, normal, slightly
  inferior, or inferior;
- inclusive and exclusive numeric or distance intervals;
- target-versus-comparable correction-rate matrices;
- subtotals and total correction rates;
- values copied between forms;
- consistency from source fact through grade and rate to final form.

Applicable criteria can change by district, land-use category, effective date,
or case. The supplied Jinshan commercial-land criteria are an example input,
not a universal rule set.

## Inputs

- Case-specific evaluation-basis documents.
- Valuation and supporting case documents used as evidence sources.
- A separately identified blank form template when completed output is requested.
- Optional reviewer-confirmed structured rules and template field maps.

## Expected outputs

- Evidence-grounded extracted facts.
- Inferred grades and deterministic correction rates.
- Review findings with warnings and unresolved items.
- A calculation trace and audit trail.
- An optional completed or corrected copy of the preserved form template.

## Intended benefit

Reduce manual comparison time and transcription errors, improve review
consistency, and establish a foundation for digital valuation review. The
system assists rather than replaces the accountable reviewer.

## Non-goals

- Automatically approve cases without evidence and validation.
- Treat LLM reasoning as authoritative arithmetic.
- Train an end-to-end PDF-to-PDF model.
- Hard-code all rules to one example district or land-use category.
- Recreate a visually similar PDF with generative AI.
- Claim support for every factor before one reliable path is demonstrated.
