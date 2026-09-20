import { describe, expect, it } from "vitest";

import { buildAnswerSummary } from "@/app/(backoffice)/admin/_lib/answerSummary";
import { QUESTIONS } from "@/lib/data/dpdpa-assessment";

describe("buildAnswerSummary", () => {
  const single = QUESTIONS.find((q) => q.options.length > 1)!;

  it("maps a stored option id to its question and option text", () => {
    const opt = single.options[1];
    const rows = buildAnswerSummary(JSON.stringify({ [single.key]: opt.id }));
    expect(rows).toContainEqual({ question: single.text, answer: opt.text });
  });

  it("joins multi-select answers with a comma and skips unknown ids", () => {
    const [a, b] = single.options;
    const rows = buildAnswerSummary(JSON.stringify({ [single.key]: [a.id, "nope", b.id] }));
    expect(rows).toContainEqual({ question: single.text, answer: `${a.text}, ${b.text}` });
  });

  it("returns nothing for empty or malformed JSON", () => {
    expect(buildAnswerSummary(undefined)).toEqual([]);
    expect(buildAnswerSummary("{not json")).toEqual([]);
    expect(buildAnswerSummary("[]")).toEqual([]);
  });
});
