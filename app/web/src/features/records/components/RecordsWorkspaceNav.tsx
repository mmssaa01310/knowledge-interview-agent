type RecordsWorkspaceNavProps = {
  activePath: string;
  recordCount: number;
  onNavigate: (path: string) => void;
};

export function RecordsWorkspaceNav({
  activePath,
  recordCount,
  onNavigate,
}: RecordsWorkspaceNavProps) {
  return (
    <div className="sidebar-section">
      <div className="workspace-nav-header">
        <strong>記録</strong>
      </div>
      <button
        type="button"
        className={activePath.startsWith("/records") ? "workspace-item active" : "workspace-item"}
        onClick={() => onNavigate("/records")}
      >
        <strong>担当記録</strong>
        <span>{recordCount}件</span>
      </button>
    </div>
  );
}
