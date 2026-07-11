export function ReferenceChatSection() {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Reference Chat</p>
          <h2>承認済みナレッジ参照チャット</h2>
          <p className="lede">正式承認済みの記録と取り込み済み文書だけを根拠に回答します。</p>
        </div>
        <button className="ghost">参照先設定</button>
      </div>
      <div className="reference-layout">
        <div className="reference-answer work-surface">
          <textarea placeholder="例: 圧入荷重が朝一にばらつく場合の一次対応は？" />
          <button className="primary">質問する</button>
          <strong>回答</strong>
          <p>
            圧入荷重の立ち上がりが遅い場合、治具清掃と位置決めピンの確認が一次対応です。
            この回答は承認済み記録 2 件と保全手順書 1 件を根拠にしています。
          </p>
        </div>
        <div className="reference-citations work-surface">
          <strong>根拠</strong>
          <ul>
            <li>承認済み記録: 圧入機A 朝一の荷重ばらつき</li>
            <li>文書: 圧入機A_保全手順.pdf p.4</li>
          </ul>
        </div>
      </div>
    </section>
  );
}
