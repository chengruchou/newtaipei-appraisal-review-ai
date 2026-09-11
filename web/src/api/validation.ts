import Ajv2020 from "ajv/dist/2020";
import type { ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import openapi from "../../openapi.json";

// Read the canonical OpenAPI schemas; do not maintain a second hand-written wire model.
const ajv = new Ajv2020({ strict: false, allErrors: false, validateFormats: true });
addFormats(ajv);
ajv.addSchema({ $id: "service", components: openapi.components });
const validators = new Map<string, ValidateFunction>();

export function validateResponse(schema: string, payload: unknown): boolean {
  let validator = validators.get(schema);
  if (!validator) {
    validator = ajv.compile({ $ref: `service#/components/schemas/${schema}` });
    validators.set(schema, validator);
  }
  return validator(payload);
}
