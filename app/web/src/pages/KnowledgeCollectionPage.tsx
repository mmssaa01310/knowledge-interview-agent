import { useState } from "react";
import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeCollectionPage(props: KnowledgeLayoutProps) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [description, setDescription] = useState("");
  const isCreateDialogOpen = props.route.name === "knowledge-new";

  if (!props.selectedKnowledgeDb) return null;

  const knowledgeDbPath = `/knowledge-dbs/${props.selectedKnowledgeDb.id}`;

  function closeDialog() {
    setName("");
    setPurpose("");
    setDescription("");
    props.navigate(knowledgeDbPath);
  }

  function submitKnowledge() {
    if (!name.trim()) return;
    props.onCreateKnowledge({
      name: name.trim(),
      purpose: purpose.trim() || undefined,
      description: description.trim() || undefined
    });
    setName("");
    setPurpose("");
    setDescription("");
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>{props.selectedKnowledgeDb.name}</h2>
          <p className="lede">このナレッジDBに登録されているナレッジ</p>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/new`)}
        >
          + 新規ナレッジ
        </button>
      </div>

      {isCreateDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog-panel" role="dialog" aria-modal="true" aria-label="新規ナレッジ作成">
            <div className="dialog-header">
              <div>
                <h2>新規ナレッジ作成</h2>
              </div>
            </div>
            <div className="form-stack">
              <label>ナレッジ名<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例: 保全ノウハウ" /></label>
              <label>用途<input value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder="例: 設備保全" /></label>
              <label>説明<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例: 現場判断と復旧手順を蓄積する" /></label>
            </div>
            <div className="dialog-actions">
              <button className="ghost" onClick={closeDialog}>キャンセル</button>
              <button className="primary" onClick={submitKnowledge} disabled={!name.trim()}>作成</button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="table-list">
        <div className="table-row table-head">
          <span>ナレッジ名</span>
          <span>用途</span>
          <span>記録数</span>
          <span>ドキュメント数</span>
          <span>更新日時</span>
        </div>
        {props.knowledges.length === 0 ? (
          <p className="empty">ナレッジがありません。「+ 新規ナレッジ」から登録してください。</p>
        ) : props.knowledges.map((knowledge) => (
          <button
            type="button"
            key={knowledge.id}
            className="table-row selectable"
            onClick={() => props.navigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}`)}
          >
            <span><strong>{knowledge.name}</strong>{knowledge.description && <small>{knowledge.description}</small>}</span>
            <span>{knowledge.purpose ?? knowledge.category ?? "-"}</span>
            <span>{knowledge.recordCount ?? 0}</span>
            <span>{knowledge.documentCount ?? 0}</span>
            <span>{formatDate(knowledge.updatedAt)}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
