type LoginPageProps = {
  onLogin: () => void;
};

export function LoginPage({ onLogin }: LoginPageProps) {
  return (
    <main className="login-page">
      <section className="login-panel">
        <p className="eyebrow">KIKIORI</p>
        <h1>AIナレッジ作成</h1>
        <p>聞く＋織る。会話から知識を引き出し、知識として織り上げます。</p>
        <p>開発環境ではデモユーザーでログインします。本番ではCognitoログインに差し替えます。</p>
        <button className="primary" onClick={onLogin}>ログイン</button>
      </section>
    </main>
  );
}
