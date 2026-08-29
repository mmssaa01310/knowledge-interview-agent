# KIKIORI UI多言語仕様

## 1. 対象範囲

この仕様は、KIKIORI Webアプリの表示言語を管理する。

対象は次のUI文言と表示形式である。

* ナビゲーション、ページタイトル、ボタン、ラベル、プレースホルダー
* ローディング、空状態、エラー、バリデーション、確認ダイアログ、Toast相当の通知
* `aria-label`、Tooltip、状態ラベル、表ヘッダー
* 日付、時刻、数値、パーセント
* HTMLの`lang`と`dir`

ユーザー入力、設備名、記録本文、回答、AIの質問・回答、ナレッジ本文はUI翻訳の対象外である。

## 2. 状態の分離

UI表示言語とAIインタビューの会話言語は別の状態として扱う。

| 状態 | 役割 | 現行実装 |
|---|---|---|
| `uiLocale` | Web UIの表示言語 | 実装済み |
| `interviewLocale` | AIインタビューで使用する言語 | 将来拡張用の型を保持 |
| `timezone` | 日付・時刻の表示タイムゾーン | Localeと分離した任意値 |

`uiLocale`の変更で`interviewLocale`または`timezone`を変更してはならない。既存のKnowledgeの`language`はAI・ナレッジ側の既存契約であり、UI Localeとして再利用しない。

## 3. 対応Locale

正式値はBCP 47形式の次のコードとする。

* `ja-JP`: 日本語
* `en-US`: English
* `zh-CN`: 简体中文
* `th-TH`: ไทย

Localeのメタデータは`app/web/src/i18n/localeMetadata.ts`で一元管理する。各エントリは少なくとも`code`、`name`、`dir`、`fallback`、`dateLocale`、`numberLocale`を持つ。

5言語目以降は、同ファイルへメタデータを追加し、同じ機能単位の翻訳ファイルと`messages.ts`のリソースを追加する。画面コンポーネントの修正は原則不要とする。

## 4. 翻訳リソースと実装

React + Viteのため、`i18next`と`react-i18next`を使用する。

翻訳ファイルは次の構成とし、日本語文字列をKeyにしない。

```text
app/web/src/i18n/locales/<locale>/
  common.json
  navigation.json
  interview.json
  knowledge.json
  settings.json
  validation.json
  errors.json
```

`app/web/src/i18n/messages.ts`が機能単位のJSONをLocaleごとに束ね、`app/web/src/i18n/locale.ts`がLocale解決・保存・現在Localeの参照を担当し、`app/web/src/i18n/index.tsx`がProviderとHTML属性更新を担当する。

UIでは意味ベースのKeyを`t("common.save")`のように使用する。AIが返す本文や保存データを表示目的だけで翻訳しない。

## 5. Locale決定と保存

クライアントでの決定順は次のとおりである。

1. ユーザーが言語切替UIで明示保存したLocale
2. `/api/me`のユーザープロファイルにある`uiLocale`
3. Cookie
4. LocalStorage
5. ブラウザーの`navigator.languages` / `navigator.language`
6. `ja-JP`

言語切替UIで選択した値は、CookieとLocalStorageへ保存する。再読み込み後も同じUI Localeを使用する。プロフィールの`uiLocale`は、明示保存値がない場合だけ適用する。

言語変更時は再ログイン・セッション再生成を要求せず、画面へ即時反映する。`<html lang="..." dir="...">`も同時に更新する。

## 6. Fallbackと検査

Fallback Localeは`ja-JP`とする。Fallbackは異常時の保険であり、翻訳漏れを運用上許容するための仕組みではない。

`app/web/scripts/check-i18n.mjs`はLocaleディレクトリを自動検出し、日本語を基準に次を検査する。

* JSON構文エラー
* Key不足
* 余分なKey
* 空文字または文字列以外のLeaf値

`pnpm --dir app/web check:i18n`で実行し、Webの本番Build前にも自動実行する。新しいLocaleディレクトリはこの検査へ自動的に含まれる。

## 7. 日付・数値・方向

日付・時刻は`Intl.DateTimeFormat`、数値・パーセントは`Intl.NumberFormat`を使用する。表示Localeは`dateLocale` / `numberLocale`から決定する。

TimezoneはLocaleから推測・変更せず、必要な場合は独立した`timezone`値を`formatDate`へ渡す。Localeメタデータの`dir`は将来のRTL Locale追加に備えて保持し、UI実装では可能な範囲でLogical Propertiesを使用する。

## 8. Backendとの境界

今回の多言語化ではBackendの業務ロジック、AI処理、保存形式、認証・認可を変更しない。Backendエラーは既存の`status`・`detail`をFrontendで分類し、利用者向けUI文言はLocaleリソースから表示する。

将来新しいBackendエラーを追加する場合は、可能な限り言語非依存のエラーコードを返し、FrontendでLocale別メッセージへ変換する。
