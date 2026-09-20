/**
 * The answer summary the survey-result email shows, rebuilt from a stored assessment's
 * `answers_json`. /api/admin/send-report used to do this server-side from QUESTIONS; the
 * question bank is pure frontend data, so the admin page builds it and sends it along.
 */
import { QUESTIONS } from "@/lib/data/dpdpa-assessment";

export type AnswerRow = { question: string; answer: string };

export function buildAnswerSummary(answersJson: string | null | undefined): AnswerRow[] {
  let answers: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(answersJson || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) answers = parsed;
  } catch {
    /* noop — same as the route: bad JSON means no summary */
  }

  const summary: AnswerRow[] = [];
  for (const q of QUESTIONS) {
    const val = answers[q.key as string];
    if (!val) continue;
    if (Array.isArray(val)) {
      const texts = (val as string[])
        .map((id) => q.options.find((o) => o.id === id)?.text)
        .filter(Boolean) as string[];
      if (texts.length) summary.push({ question: q.text, answer: texts.join(", ") });
    } else {
      const opt = q.options.find((o) => o.id === val);
      if (opt) summary.push({ question: q.text, answer: opt.text });
    }
  }
  return summary;
}
