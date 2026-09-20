/**
 * `buildAnswerSummary` moved from the deleted route handler into SurveyClient, because
 * the question and option text stays in the frontend (docs/build/inventory/assessments.md).
 * Same behaviour the route handler had.
 */
import { describe, expect, it } from "vitest";
import { QUESTIONS } from "@/lib/data/dpdpa-assessment";
import { buildAnswerSummary } from "@/app/[locale]/assessment/SurveyClient";

const sectorQ = QUESTIONS.find((q) => q.key === "q1_sector")!;
const multiQ = QUESTIONS.find((q) => q.type === "multi")!;

describe("buildAnswerSummary", () => {
  it("renders a single-choice answer as its option text", () => {
    const summary = buildAnswerSummary({ [sectorQ.key]: sectorQ.options[0].id });

    expect(summary).toEqual([{ question: sectorQ.text, answer: sectorQ.options[0].text }]);
  });

  it("joins a multi-choice answer with commas", () => {
    const [a, b] = multiQ.options;

    const summary = buildAnswerSummary({ [multiQ.key]: [a.id, b.id] });

    expect(summary).toEqual([{ question: multiQ.text, answer: `${a.text}, ${b.text}` }]);
  });

  it("skips unanswered questions", () => {
    expect(buildAnswerSummary({})).toEqual([]);
    expect(buildAnswerSummary({ [sectorQ.key]: "" })).toEqual([]);
  });

  it("skips an option id that is not in the catalogue", () => {
    expect(buildAnswerSummary({ [sectorQ.key]: "not-a-real-option" })).toEqual([]);
    expect(buildAnswerSummary({ [multiQ.key]: ["not-a-real-option"] })).toEqual([]);
  });

  it("ignores keys that are not questions", () => {
    expect(buildAnswerSummary({ some_other_field: "value" })).toEqual([]);
  });

  it("keeps the catalogue order, not the answer order", () => {
    const first = QUESTIONS[0];
    const second = QUESTIONS[1];
    const answers: Record<string, unknown> = {};
    answers[second.key] = second.options[0].id;
    answers[first.key] = first.options[0].id;

    const summary = buildAnswerSummary(answers);

    expect(summary.map((r) => r.question)).toEqual([first.text, second.text]);
  });
});
