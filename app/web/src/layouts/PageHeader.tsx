type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  description?: string;
  backAction?: React.ReactNode;
  actions?: React.ReactNode;
};

export function PageHeader({ eyebrow, title, description, backAction, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header-copy">
        {backAction}
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h1>{title}</h1>
          {description ? <p className="lede">{description}</p> : null}
        </div>
      </div>
      {actions}
    </header>
  );
}
