import { useState } from "react";
import { evidenceBox } from "@/ui/evidenceGeometry";
import contracts from "./contracts.json";
import { type Category, type LocalPrivacyClient, type ReviewSnapshot, versionOf } from "./client";

const categories = contracts.components.schemas.SensitiveCategory.enum as Category[];

export function RegionEditor({
  client,
  snapshot,
  page,
  disabled,
  run,
}: {
  client: LocalPrivacyClient;
  snapshot: ReviewSnapshot;
  page: number;
  disabled: boolean;
  run: (operation: () => Promise<ReviewSnapshot>) => void;
}) {
  const selections = snapshot.view.command.selections.filter(
    (s) => s.candidate.region.page === page,
  );
  const [selected, setSelected] = useState("");
  const [coords, setCoords] = useState(["", "", "", ""]);
  const [category, setCategory] = useState<Category>("name");
  const [entity, setEntity] = useState("");
  const [reason, setReason] = useState("");
  const dimensions = snapshot.view.command.source.pages.find((p) => p.number === page);
  const box = coords.map(Number) as [number, number, number, number];
  const valid =
    dimensions &&
    coords.every((v) => v.trim() !== "") &&
    evidenceBox(box, dimensions.width, dimensions.height) !== null;
  const version = versionOf(snapshot);
  function choose(identifier: string) {
    setSelected(identifier);
    const current = selections.find((s) => s.candidate.candidate_id === identifier);
    setCoords(current ? current.candidate.region.bbox.map(String) : ["", "", "", ""]);
    setCategory(current?.candidate.category ?? "name");
    setEntity(current?.entity_id ?? "");
    setReason("");
  }
  return (
    <fieldset disabled={disabled}>
      <legend>Review redaction regions</legend>
      <ul>
        {selections.map((s) => (
          <li key={s.candidate.candidate_id}>
            <strong>{s.candidate.category}</strong>:{" "}
            {s.candidate.raw_text ?? "Image region; inspect the original page"} — {s.disposition}.
            Confidence: {s.candidate.confidence ?? "unavailable"}. Region:{" "}
            {s.candidate.region.bbox.join(", ")}.
          </li>
        ))}
      </ul>
      <label>
        Region to edit
        <select value={selected} onChange={(e) => choose(e.target.value)}>
          <option value="">Add a new region</option>
          {selections.map((s) => (
            <option key={s.candidate.candidate_id} value={s.candidate.candidate_id}>
              {s.candidate.category}: {s.candidate.candidate_id}
            </option>
          ))}
        </select>
      </label>
      <p>
        Coordinates use PDF points from the bottom-left of this unrotated page. Page dimensions:{" "}
        {dimensions?.width} × {dimensions?.height}.
      </p>
      {["Left x", "Bottom y", "Right x", "Top y"].map((label, index) => (
        <label key={label}>
          {label}
          <input
            inputMode="decimal"
            value={coords[index]}
            onChange={(e) =>
              setCoords((previous) => previous.map((v, i) => (i === index ? e.target.value : v)))
            }
          />
        </label>
      ))}
      <label>
        Region category
        <select value={category} onChange={(e) => setCategory(e.target.value as Category)}>
          {categories.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
      </label>
      {selected ? (
        <label>
          Entity group
          <select value={entity} onChange={(e) => setEntity(e.target.value)}>
            <option value="">Split into a new entity</option>
            {[
              ...new Set(
                snapshot.view.command.selections.flatMap((s) => (s.entity_id ? [s.entity_id] : [])),
              ),
            ].map((group) => (
              <option key={group}>{group}</option>
            ))}
          </select>
        </label>
      ) : null}
      <button
        type="button"
        disabled={!valid}
        onClick={() =>
          run(() =>
            selected
              ? client.edit({
                  ...version,
                  candidate_id: selected,
                  region: { page, bbox: box },
                  category,
                  entity_id: entity || null,
                })
              : client.add({ ...version, region: { page, bbox: box }, category }),
          )
        }
      >
        {selected ? "Save region correction" : "Add redaction region"}
      </button>
      {selected ? (
        <>
          <label>
            Reason for dismissing this detection
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button
            type="button"
            disabled={!reason.trim()}
            onClick={() => run(() => client.remove({ ...version, candidate_id: selected, reason }))}
          >
            Dismiss detection with this reason
          </button>
        </>
      ) : null}
    </fieldset>
  );
}
