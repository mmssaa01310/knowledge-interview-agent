type CreateKnowledgeDbDialogProps = {
  isOpen: boolean;
  isCreating: boolean;
  error: string;
  name: string;
  description: string;
  onNameChange: (value: string) => void;
  onDescriptionChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export type { CreateKnowledgeDbDialogProps };

export function CreateKnowledgeDbDialog(props: CreateKnowledgeDbDialogProps) {
  if (!props.isOpen) return null;

  return (
    <div className="dialog-backdrop" role="presentation">
      <form
        className="dialog-panel"
        role="dialog"
        aria-modal="true"
        aria-label="ナレッジ登録"
        onSubmit={(event) => {
          event.preventDefault();
          props.onSubmit();
        }}
      >
        <div className="dialog-header">
          <div>
            <h2>ナレッジ登録</h2>
          </div>
        </div>
        <label className="field-group">
          <span>ナレッジ名</span>
          <input
            autoFocus
            value={props.name}
            onChange={(event) => props.onNameChange(event.target.value)}
            placeholder="例: 保全トラブル対応ナレッジ"
            disabled={props.isCreating}
          />
        </label>
        <label className="field-group">
          <span>説明</span>
          <textarea
            value={props.description}
            onChange={(event) => props.onDescriptionChange(event.target.value)}
            placeholder="例: 保全トラブル対応の暗黙知を蓄積する"
            disabled={props.isCreating}
          />
        </label>
        {props.error && <p className="notice error">{props.error}</p>}
        <div className="dialog-actions">
          <button
            type="button"
            className="ghost"
            onClick={props.onClose}
            disabled={props.isCreating}
          >
            キャンセル
          </button>
          <button
            type="submit"
            className="primary"
            disabled={props.isCreating || !props.name.trim()}
          >
            {props.isCreating ? "登録中" : "登録"}
          </button>
        </div>
      </form>
    </div>
  );
}