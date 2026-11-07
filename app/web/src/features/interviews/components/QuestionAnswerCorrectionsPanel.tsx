import { useEffect, useMemo, useState } from "react";
import type { InterviewState } from "../../../types/app";
import { useI18n } from "../../../i18n";

type QuestionAnswerCorrectionsPanelProps = {
  interviewState: InterviewState | null;
  canEdit: boolean;
  onSave: (questionId: string, answer: string, baseStateVersion: number) => Promise<void>;
};

export function QuestionAnswerCorrectionsPanel({
  interviewState,
  canEdit,
  onSave,
}: QuestionAnswerCorrectionsPanelProps) {
  const { t } = useI18n();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [baseStateVersion, setBaseStateVersion] = useState(0);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const answers = interviewState?.questionAnswers ?? {};
  const questions = useMemo(() => new Map(
    (interviewState?.askedQuestions ?? []).map((question) => [question.questionId, question]),
  ), [interviewState?.askedQuestions]);
  const rows = Object.entries(answers)
    .filter(([, answer]) => (
      ["ANSWERED", "PARTIAL"].includes(answer.answerStatus ?? "")
      && Boolean(answer.answer?.trim())
    ))
    .map(([questionId, answer]) => ({
      questionId,
      answer,
      question: questions.get(questionId),
    }));

  useEffect(() => {
    if (editingId) return;
    setDrafts(Object.fromEntries(rows.map(({ questionId, answer }) => [questionId, answer.answer ?? ""])));
    setBaseStateVersion(interviewState?.stateVersion ?? 0);
  }, [interviewState?.stateVersion, interviewState?.questionAnswers, editingId]);

  if (!rows.length) return null;

  async function save(questionId: string) {
    if (savingId) return;
    const answer = (drafts[questionId] ?? "").trim();
    if (!answer) return;
    setSavingId(questionId); setNotice("");
    try {
      await onSave(questionId, answer, baseStateVersion);
      setEditingId(null);
      setNotice(t("interview.questionAnswers.saved"));
    } catch {
      setNotice(t("interview.questionAnswers.saveFailed"));
    } finally {
      setSavingId(null);
    }
  }

  return (
    <section className="question-answer-corrections" aria-label={t("interview.questionAnswers.title")}>
      <header className="system-progress-header">
        <div>
          <strong>{t("interview.questionAnswers.title")}</strong>
          <p>{t("interview.questionAnswers.description")}</p>
        </div>
      </header>
      <div className="system-progress-list">
        {rows.map(({ questionId, answer, question }) => {
          const value = drafts[questionId] ?? answer.answer ?? "";
          const changed = value.trim() !== (answer.answer ?? "").trim();
          const isEditing = editingId === questionId;
          return (
            <article className="system-progress-item confirmed" key={questionId}>
              <div className="system-progress-item-header">
                <div className="system-progress-item-title">
                  <strong>{question?.text || t("interview.questionAnswers.unnamed")}</strong>
                </div>
                <span className="status-pill muted">
                  {answer.answerStatus === "PARTIAL" ? t("interview.answerStatus.candidate") : t("interview.answerStatus.confirmed")}
                </span>
              </div>
              {canEdit && isEditing ? (
                <div className="interview-answer-block">
                  <textarea
                    className="interview-answer-input"
                    value={value}
                    onChange={(event) => setDrafts((current) => ({ ...current, [questionId]: event.target.value }))}
                    aria-label={t("interview.questionAnswers.answerAria", { question: question?.text || t("interview.questionAnswers.unnamed") })}
                    maxLength={10000}
                  />
                  <button
                    type="button"
                    className="ghost compact"
                    onClick={() => setEditingId(null)}
                    disabled={savingId === questionId}
                  >
                    {t("common.cancel")}
                  </button>
                  <button
                    type="button"
                    className="primary compact"
                    onClick={() => void save(questionId)}
                    disabled={!changed || !value.trim() || Boolean(savingId)}
                  >
                    {savingId === questionId ? t("common.saving") : t("common.save")}
                  </button>
                </div>
              ) : (
                <>
                  <p>{answer.answer}</p>
                  {canEdit ? <button
                    type="button"
                    className="ghost compact"
                    onClick={() => {
                      setDrafts((current) => ({ ...current, [questionId]: answer.answer ?? "" }));
                      setBaseStateVersion(interviewState?.stateVersion ?? 0);
                      setNotice("");
                      setEditingId(questionId);
                    }}
                    disabled={Boolean(editingId) || Boolean(savingId)}
                  >{t("common.edit")}</button> : null}
                </>
              )}
            </article>
          );
        })}
      </div>
      {notice ? <p className="notice" role="status">{notice}</p> : null}
    </section>
  );
}
