import { useEffect, useState } from "react";
import { formatDate } from "../lib/date";
import type { KnowledgeLayoutProps } from "../types/pageProps";

export function KnowledgeCollectionPage(props: KnowledgeLayoutProps) {
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [description, setDescription] = useState("");
  const [createKnowledgeDbId, setCreateKnowledgeDbId] = useState(props.selectedKnowledgeDb?.id ?? "");
  const isCreateDialogOpen = props.route.name === "knowledge-new";

  useEffect(() => {
    if (!isCreateDialogOpen || !props.selectedKnowledgeDb) return;
    setCreateKnowledgeDbId(props.selectedKnowledgeDb.id);
  }, [isCreateDialogOpen, props.selectedKnowledgeDb?.id]);

  if (!props.selectedKnowledgeDb) return null;

  const knowledgeDbPath = `/knowledge-dbs/${props.selectedKnowledgeDb.id}`;
  const currentKnowledges = props.knowledges.filter((knowledge) => knowledge.knowledgeDbId === props.selectedKnowledgeDb?.id);

  function closeDialog() {
    setName("");
    setPurpose("");
    setDescription("");
    props.navigate(knowledgeDbPath);
  }

  function submitKnowledge() {
    if (!name.trim()) return;
    props.onCreateKnowledge(
      {
        name: name.trim(),
        purpose: purpose.trim() || undefined,
        description: description.trim() || undefined
      },
      createKnowledgeDbId,
    );
    setName("");
    setPurpose("");
    setDescription("");
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>ナレッジ一覧</h2>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => props.navigate(`/knowledge-dbs/${props.selectedKnowledgeDb?.id}/knowledges/new`)}
        >
          + ナレッジを作成
        </button>
      </div>
      {props.knowledgeCreationError ? <p className="notice error">{props.knowledgeCreationError}</p> : null}

      {isCreateDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <div className="dialog-panel" role="dialog" aria-modal="true" aria-label="新規ナレッジ作成">
            <div className="dialog-header">
              <div>
                <h2>新規ナレッジ作成</h2>
                <p>作成後にインタビュー設定を行います。</p>
              </div>
            </div>
            <div className="form-stack">
              <label>ナレッジ名<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例: 保全ノウハウ" /></label>
              <label>用途<input value={purpose} onChange={(event) => setPurpose(event.target.value)} placeholder="例: 設備保全" /></label>
              <label>説明<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例: 現場判断と復旧手順を蓄積する" /></label>
              {props.knowledgeDbs.length > 1 ? (
                <label>業務領域
                  <select value={createKnowledgeDbId} onChange={(event) => setCreateKnowledgeDbId(event.target.value)}>
                    {props.knowledgeDbs.map((knowledgeDb) => <option key={knowledgeDb.id} value={knowledgeDb.id}>{knowledgeDb.name}</option>)}
                  </select>
                </label>
              ) : null}
            </div>
            <div className="dialog-actions">
              <button className="ghost" onClick={closeDialog}>キャンセル</button>
              <button className="primary" onClick={submitKnowledge} disabled={!name.trim()}>作成して設定へ</button>
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
        {currentKnowledges.length === 0 ? (
          <p className="empty">ナレッジがありません。「+ ナレッジを作成」から登録してください。</p>
        ) : currentKnowledges.map((knowledge) => (
          <button
            type="button"
            key={knowledge.id}
            className="table-row selectable"
            onClick={() => props.navigate(`/knowledge-dbs/${knowledge.knowledgeDbId}/knowledges/${knowledge.id}/interview`)}
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
