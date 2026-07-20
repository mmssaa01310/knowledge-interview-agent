import type { KnowledgeField } from "../../lib/api";

type KnowledgeFieldsSectionProps = {
  fields: KnowledgeField[];
};

export function KnowledgeFieldsSection({ fields }: KnowledgeFieldsSectionProps) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Interview Fields</p>
          <h2>ヒアリング項目設定</h2>
          <p className="lede">AIが聞き取る項目と、人が承認する構造化データの枠を定義します。</p>
        </div>
        <button className="primary">項目追加</button>
      </div>
      <div className="table-list">
        <div className="table-row table-head field-row">
          <span>順序</span>
          <span>項目名</span>
          <span>入力形式</span>
          <span>必須</span>
          <span>AI質問</span>
        </div>
        {fields.length === 0 ? (
          <p className="empty">ヒアリング項目はまだ定義されていません。</p>
        ) : fields.map((field) => (
          <div key={`${field.displayOrder}-${field.name}`} className="table-row field-row">
            <span>{field.displayOrder}</span>
            <span>
              <strong>{field.name}</strong>
              <small>{field.aiQuestionExamples?.[0] ?? "質問例は未設定"}</small>
            </span>
            <span>{field.inputType}</span>
            <span>{field.required ? "必須" : "任意"}</span>
            <span>{field.askByAi ? "対象" : "対象外"}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
