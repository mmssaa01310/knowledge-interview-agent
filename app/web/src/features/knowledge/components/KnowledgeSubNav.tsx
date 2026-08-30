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

function InterviewIcon() {
  return (
    <svg className="sub-nav-svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="7.5" cy="7.5" r="2.5" />
      <path d="M3.5 17c.4-2.2 1.8-3.5 4-3.5s3.6 1.3 4 3.5" />
      <path d="M13.5 5.5h4.5a2 2 0 0 1 2 2v3.2a2 2 0 0 1-2 2h-1.4l-2.1 2v-2h-1a2 2 0 0 1-2-2V7.5a2 2 0 0 1 2-2Z" />
      <path d="M15.5 8.2h2.5M15.5 10.2h1.5" />
    </svg>
  );
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
              {item.key === "interview" ? <InterviewIcon /> : <span className="sub-nav-icon records" aria-hidden="true" />}
              {t(`navigation.${item.key}`)}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
