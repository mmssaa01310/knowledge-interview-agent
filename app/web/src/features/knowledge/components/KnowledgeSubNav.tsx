type KnowledgeSubNavProps = {
  knowledgeDbId: string;
  knowledgeId: string;
  activePath: string;
  onNavigate: (path: string) => void;
};

function isBranchPath(activePath: string, targetPath: string) {
  return activePath === targetPath || activePath.startsWith(`${targetPath}/`);
}

export function KnowledgeSubNav({ knowledgeDbId, knowledgeId, activePath, onNavigate }: KnowledgeSubNavProps) {
  const basePath = `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}`;
  const items = [
    { label: "概要", path: basePath, exact: true },
    { label: "記録", path: `${basePath}/records`, exact: false },
    { label: "項目設定", path: `${basePath}/settings`, exact: true },
    { label: "ドキュメント", path: `${basePath}/documents`, exact: true }
  ];

  return (
    <nav className="sub-nav" aria-label="ナレッジDB内メニュー">
      <div className="sub-nav-items">
        {items.map((item) => {
          const active = item.exact ? activePath === item.path : isBranchPath(activePath, item.path);
          return (
            <button
              type="button"
              key={item.path}
              className={active ? "sub-nav-item active" : "sub-nav-item"}
              onClick={() => onNavigate(item.path)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        className="sub-nav-item sub-nav-return"
        onClick={() => onNavigate(`/knowledge-dbs/${knowledgeDbId}`)}
      >
        ノウハウ登録に戻る
      </button>
    </nav>
  );
}
