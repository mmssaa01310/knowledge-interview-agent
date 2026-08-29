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
    { label: "インタビュー", path: `${basePath}/interview`, exact: true },
    { label: "記録", path: `${basePath}/records`, exact: false }
  ];

  return (
    <nav className="sub-nav" aria-label="ナレッジ内メニュー">
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
    </nav>
  );
}
