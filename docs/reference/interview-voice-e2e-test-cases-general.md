# インタビュー音声 E2E テストケース

## 目的

この文書は、テキスト評価用のテストではなく、最終的に実音声を入力する E2E テストへ変換するための会話シナリオを定義する。各ケースは、発話内容だけでなく、Dialogue Act、回答充足性、FieldState、`questionId`、次の応答を機械的に検証できる形へ変換する。

音声 E2E では、少なくとも次の境界を含めて検証する。

```text
音声ファイル / マイク入力
  → Transcribe 確定文字列
  → Structured Interpreter
  → Backend 状態遷移
  → 必要な検索 / Question Generator
  → Polly 音声
  → UI 表示・再生開始
```

回答評価と状態遷移の正本は `app/api` にあり、音声認識・音声再生の正本は `app/voice` にある。音声条件付きケースは、同じシナリオをテキストで代用せず、音声ファイルで実行する。

`interview_voice_e2e_test_cases-system.md`、`interview_voice_e2e_test_cases-work-flow.md`、
`interview_voice_e2e_test_cases-hozen.md`、`interview_voice_e2e_test_cases-unmokuchi.md` は、
この文書の一般ケースを含む累積資料である。テストランナーは5ファイルをglobで収集せず、ケースIDを明示して一度だけ実行する。

## 共通検証項目

各ターンで次の値を採取する。

1. Transcribe の確定文字列、`sttConfidence`
2. Dialogue Act
3. 回答充足性 / `answerResolution`
4. 対象 Field の `FieldState`
5. 現在および次の `questionId`
6. 応答文、応答 action
7. Question Generator と文書検索の呼び出し回数
8. 必要に応じて `voice_turn_id` ごとの遅延メトリクス

「同じ質問をしない」は、質問文の類似度だけでなく、同一ターン内で `questionId` が不要に新しくなっていないことも確認する。

### 現行スキーマへの対応

ケース表の短縮ラベルは、実行時には次のStructured Interview出力へ正規化する。

| ケース表のラベル | 実行時の値 |
|---|---|
| `PARTIAL_ANSWER` | `dialogueAct=ANSWER` + `answerAssessment.sufficiency=PARTIAL` |
| `UNANSWERABLE` / `USER_UNSURE` | `dialogueAct=ANSWER` + `answerAssessment.sufficiency=UNANSWERABLE` |
| `REFUSAL` | `dialogueAct=ANSWER` + `answerAssessment.sufficiency=REFUSAL` |
| `STT_ERROR` | `transcriptAssessment.correctionStatus=UNCERTAIN` |
| `QUALIFIED_CONFIRMATION` | `CORRECTION`を優先し、少なくとも`CONFIRMATION`にはしない |
| `ALREADY_ANSWERED` / `COMPLAINT` / `META` | `OTHER`等のDialogue Actと、既回答参照・応答意図を別々に検証 |

`general_interview`、`system_requirements`、`business_flow`、`maintenance_record`、`tacit_knowledge`は資料上のカテゴリ名であり、BackendのProfile値へ直接渡さない。Profileは`fixed_form`、`business_process`、`system_requirement`のいずれかに明示的に対応付ける。

## 01. 一般インタビュー：基本テストケース

| ID | パターン | 質問 | ユーザー発話 | 期待する判定 | 期待する動作 |
|---|---|---|---|---|---|
| GEN-001 | 正常回答 | 現在のお仕事を教えてください | システム開発をしています | `ANSWER` | 回答を保存し次へ進む |
| GEN-002 | 詳細回答 | 現在のお仕事を教えてください | 社内システムの開発と AWS のインフラを担当しています | `ANSWER` | 複数情報を保存し次へ進む |
| GEN-003 | 部分回答 | 現在のお仕事を教えてください | 会社員です | `PARTIAL_ANSWER` | 職種を具体的に深掘りする |
| GEN-004 | 極端に短い回答 | 現在のお仕事を教えてください | 開発です | `ANSWER` / `PARTIAL` | 同じ質問を繰り返さず、必要なら具体化する |
| GEN-005 | 質問が分からない | ご自身の強みを教えてください | どういう意味ですか？ | `CLARIFICATION_REQUEST` | 質問を言い換える。聞き取り失敗として扱わない |
| GEN-006 | 答えが思いつかない | ご自身の強みを教えてください | よく分からないですね | `UNANSWERABLE` / `USER_UNSURE` | 具体例を提示する。聞き取り失敗として扱わない |
| GEN-007 | 再度分からない | ご自身の強みを教えてください | やっぱり思いつかないです | `USER_UNSURE` | 別角度から質問するか、スキップを提案する |
| GEN-008 | 回答なし | ご自身の強みを教えてください | ありません | `ANSWER` | 「ない」という回答を受理する |
| GEN-009 | フィラー | 現在のお仕事を教えてください | うーん | `HESITATION` | `questionId` と FieldState を維持し、短く促す |
| GEN-010 | 相槌 | 現在のお仕事を教えてください | へえ | `BACKCHANNEL` / `OTHER` | 新しい質問を生成しない |
| GEN-011 | 考え中 | 転機となった経験を教えてください | そうですねえ…… | `HESITATION` | 待つか、短く促す |
| GEN-012 | 既回答指摘 | どんな仕事をしていますか？ | さっき開発って答えました | `ALREADY_ANSWERED` | 過去回答を参照し、同じ質問をしない |
| GEN-013 | 訂正 | 現在のお住まいは滋賀県ですね？ | いや、京都です | `CORRECTION` | 滋賀県を確定せず、京都へ修正する |
| GEN-014 | 混在確認 | 強みはアプリ開発という理解でよいですか？ | ちょっと違うけど、まあいいです | `CORRECTION` / `QUALIFIED_CONFIRMATION` | `CONFIRMED` にせず、差分または訂正内容を確認する |
| GEN-015 | 明示確認 | 強みはアプリ開発という理解でよいですか？ | はい、それで合っています | `CONFIRMATION` | 候補を `CONFIRMED` にする |
| GEN-016 | 明示否定 | 強みはアプリ開発という理解でよいですか？ | いや、全然違います | `REJECTION` | 候補を確定せず、再確認または再回答を促す |
| GEN-017 | 言い直し | 年齢を教えてください | 31……いや、32歳です | `ANSWER` + `CORRECTION` | 32歳を採用し、31歳を確定しない |
| GEN-018 | 自己訂正 | 所属部署を教えてください | 開発一課、あ、二課です | `ANSWER` + `CORRECTION` | 開発二課を採用する |
| GEN-019 | 複数回答 | お名前を教えてください | 宮崎です。32歳で滋賀県に住んでいます | `ANSWER_MULTI_FIELD` | 名前・年齢・地域をまとめて保存する |
| GEN-020 | 質問外情報 | お名前を教えてください | システム開発をしています | `OFF_TOPIC` / `ANSWER_OTHER_FIELD` | 情報は保持し、名前を自然に再質問する |

## 02. 会話品質ケース

| ID | パターン | 質問 | ユーザー発話 | 期待する動作 |
|---|---|---|---|---|
| GEN-021 | 曖昧回答 | 今後の目標を教えてください | いろいろやりたいですね | 何をやりたいか一段階深掘りする |
| GEN-022 | 抽象回答 | 仕事の課題はありますか？ | 効率ですね | 「何の効率か」を聞く |
| GEN-023 | 具体例あり | 強みを教えてください | 作ったアプリがお客さんから評価されました | 強みを断定せず、何が評価されたか深掘りする |
| GEN-024 | エピソード回答 | 転機を教えてください | 初めてお客さんから直接ありがとうと言われたことです | 何が変わったか深掘りする |
| GEN-025 | 拒否 | 年齢を教えてください | それは答えたくないです | `REFUSAL` として受理し、執拗に再質問しない |
| GEN-026 | スキップ希望 | 強みを教えてください | これは飛ばしていいですか？ | `SKIP` として扱い、次へ進む |
| GEN-027 | 質問返し | 今後の目標を教えてください | 例えばどういうものですか？ | 例を出して説明する |
| GEN-028 | 脱線 | 現在のお仕事を教えてください | 昨日すごい雨だったんですよ | `OFF_TOPIC` として扱い、現在の質問へ戻す |
| GEN-029 | 不満表明 | 強みを教えてください | さっきから同じことばかり聞いてません？ | `COMPLAINT` / `META` として受け止め、質問重複を避ける |
| GEN-030 | 修正要求 | さきほどの回答でよいですか？ | やっぱり前の回答を変えたいです | `CORRECTION_REQUEST` として前の候補を再編集する |

## 03. STT・音声特有ケース

このカテゴリは実際の音声ファイルで実行する。音声ファイルの条件以外は、対応する一般ケースと同じ期待値を使う。

| ID | 音声条件 | 発話 | 評価ポイント |
|---|---|---|---|
| GEN-A01 | 通常音声 | 宮崎です | 基準となる ASR 精度 |
| GEN-A02 | 小声 | 宮崎です | 小声での ASR 精度 |
| GEN-A03 | 早口 | システム開発を担当しています | 早口での ASR 精度 |
| GEN-A04 | 遅い・間あり | システム……開発です | 未完発話や STT 失敗と誤判定しない |
| GEN-A05 | フィラー付き | えーっと、システム開発です | 意味のある回答として `ANSWER` 処理する |
| GEN-A06 | 言い直し | 31歳、いや32歳です | 最終値 32 歳を採用する |
| GEN-A07 | 関西寄り口語 | よう分からんわ | 意味的な非回答として扱い、`STT_ERROR` にしない |
| GEN-A08 | 語尾が小さい | 開発を担当してます…… | 正常回答として扱う |
| GEN-A09 | 背景雑音あり | 滋賀県です | ASR confidence と補正状態を確認する |
| GEN-A10 | 固有名詞 | 株式会社 Sontana です | 固有名詞を保持する |

## Critical ケース

以下は 1 件でも期待値を外した場合に Fail とする。

| ID | 発話 | 必須条件 |
|---|---|---|
| GEN-012 | さっき答えました | 同一質問の再出力を禁止 |
| GEN-014 | ちょっと違うけど、まあいい | `CONFIRMED` と `CONFIRMATION` を禁止 |
| GEN-005 | どういう意味ですか？ | 「聞き取れませんでした」を禁止 |
| GEN-006 | よく分からない | `STT_ERROR` と「聞き取れませんでした」を禁止 |
| GEN-009 | うーん | 新しい `questionId` の生成を禁止 |
| GEN-017 | 31、いや32 | 31 の確定を禁止 |
| GEN-025 | 答えたくない | 執拗な再質問を禁止 |

## 音声 E2E 用の正規化フォーマット

各ケースは、音声ファイルと実行コンテキストを結び付け、次の形式へ変換する。

```yaml
id: GEN-014
category: general_interview
critical: true

audio:
  # E2E 実装時に実ファイルのパスへ置き換える
  text: "ちょっと違うけど、まあいいです"
  variation: normal
  file: null

context:
  question: "強みはアプリ開発という理解でよいですか？"
  current_field: strengths
  current_state: AWAITING_CONFIRMATION

expected:
  dialogue_act:
    allow:
      - CORRECTION
      - QUALIFIED_CONFIRMATION

  field_state:
    must_not_be:
      - CONFIRMED

  question_id:
    behavior: KEEP_OR_CORRECTION_FLOW

  response:
    intent:
      - ASK_DIFFERENCE
      - CLARIFY_CORRECTION

forbidden:
  dialogue_act:
    - CONFIRMATION

  response_contains:
    - "聞き取れませんでした"
```

## E2E 変換時の判定ルール

- 音声の `text` は期待値ではなく、Transcribe の実出力を評価する入力とする。
- `dialogue_act.allow` は、同じ会話制御を表す複数の許容値を定義する。許容値以外は Fail とする。
- `must_not_be` は、状態遷移後の実状態に対する禁止条件である。
- `question_id.behavior` は、単純な一致だけでなく、同一維持・訂正フロー・次ターゲット変更を表現する。
- `forbidden.response_contains` は、意味的な非回答を STT 聞き取り失敗として返す回帰を検出する。
- Polly の再生開始まで確認するケースでは、`voice_turn_id` と `response_id` を保存し、API 評価完了から最初の音声チャンクまでを追跡する。

## 実装時の配置

この Markdown はテストシナリオの正規資料とし、実行コードは用途ごとに分離する。

重要な回帰ケース（`GEN-005`、`GEN-006`、`GEN-009`、`GEN-012`、`GEN-014`、`GEN-017`、`GEN-025`）の機械可読な契約は、APIテストfixtureの
`app/api/tests/fixtures/interview_voice_critical_cases.json` に同期して管理する。fixtureは現時点では
`deterministic_api`用であり、`audio.file: null` のケースを実音声E2Eの成功とはみなさない。

- API の状態遷移・Dialogue Act 回帰: `app/api/tests/services/`
- API の音声セッション契約: `app/api/tests/contract/`
- Transcribe / Polly Runtime: `app/voice/tests/unit/transcribe_polly/`
- 実音声・ブラウザ・AWS を含む E2E: 将来の E2E 専用テストランナー配下

実装コードへ変換した後も、この文書の ID（例: `GEN-014`）をテスト名・ログ・レポートに残し、テキスト単体テストと音声 E2E テストの対応を追跡できるようにする。
