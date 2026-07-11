type ChatbotSubNavProps = {
  chatbotId: string;
  activePath: string;
  onNavigate: (path: string) => void;
};

export function ChatbotSubNav({ chatbotId, activePath, onNavigate }: ChatbotSubNavProps) {
  const items = [
    { label: "概要", path: `/chatbots/${chatbotId}` },
    { label: "チャット", path: `/chatbots/${chatbotId}/chat` },
    { label: "参照設定", path: `/chatbots/${chatbotId}/references` }
  ];

  return (
    <nav className="sub-nav" aria-label="チャットボット内メニュー">
      {items.map((item) => (
        <button
          type="button"
          key={item.path}
          className={activePath === item.path ? "sub-nav-item active" : "sub-nav-item"}
          onClick={() => onNavigate(item.path)}
        >
          {item.label}
        </button>
      ))}
    </nav>
  );
}
