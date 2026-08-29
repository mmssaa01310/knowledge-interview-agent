import { useI18n } from "../../../i18n";

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
  const { t } = useI18n();
  const basePath = `/knowledge-dbs/${knowledgeDbId}/knowledges/${knowledgeId}`;
  const items = [
    { key: "interview", path: `${basePath}/interview`, exact: true },
    { key: "records", path: `${basePath}/records`, exact: false }
  ];

  return (
    <nav className="sub-nav" aria-label={t("navigation.knowledgeMenu")} role="tablist">
      <div className="sub-nav-items">
        {items.map((item) => {
          const active = item.exact ? activePath === item.path : isBranchPath(activePath, item.path);
          return (
            <button
              type="button"
              key={item.path}
              role="tab"
              aria-selected={active}
              className={active ? "sub-nav-item active" : "sub-nav-item"}
              onClick={() => onNavigate(item.path)}
            >
              <span className={`sub-nav-icon ${item.key === "interview" ? "conversation" : "records"}`} aria-hidden="true" />
              {t(`navigation.${item.key}`)}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
