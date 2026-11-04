# Twitchゲーム別視聴者数の取得調査

調査日: 2026-11-04（JST）

## 結論と確認範囲

公式Twitch APIの仕様上、ゲーム別の同時視聴者数を集計できる。
ただし、認証済みの実データ取得は未検証。今回実施したのは公式仕様の確認と、認証なしでのAPI疎通確認である。
これは初回調査時点の記録である。現行の実装仕様・設定手順はREADME.mdを参照。

## 現在の取得方法（2026-11-05変更）

1. `GET https://api.twitch.tv/helix/streams?first=100&type=live` で、全体のライブ配信を視聴者数順に取得する。
2. カーソルでページを送り、重複する配信IDを除いた最大1,000件を選ぶ。一覧が終われば、その件数で終了する。
3. 取得範囲のカテゴリを `GET /helix/games?id=...` で調べ、IGDB ID登録済みゲームの配信だけをゲーム別に合計する。
4. 取得配信数・集計対象配信数とともに保存し、ゲーム別の合計人数順にHTMLを出力する。100タイトルへの絞り込みはしない。

これは各ゲームの全配信の合計ではない。雑談などを除外した分を1,001件目以降から補充しない。ページ取得中の変動による重複は除外できるが、欠落は解消できない。通信失敗を取得終了や0人として扱わない。

## 実際の疎通結果

2026-11-04 23:04〜23:05 JSTに次の読み取り専用リクエストを実行した。

```sh
curl --silent --show-error --max-time 20 \
  --write-out '\nHTTP_STATUS=%{http_code}\n' \
  'https://api.twitch.tv/helix/games/top?first=1'
```

応答:

```text
{"error":"Unauthorized","status":401,"message":"OAuth token is missing"}
HTTP_STATUS=401
```

APIには到達したが、認証が必要である。ゲーム名や視聴者数の取得成功を示す結果ではない。
作業ディレクトリには既存ソースや認証設定ファイルがなく、有効なGit Repositoryとしても認識されなかった。

## 実データ検証に必要な準備

- Twitchアカウントでメール確認・二要素認証を済ませ、Developer Consoleからアプリを登録する。
- 自動収集には、Client IDとClient SecretからApp Access Tokenを取得する方式を利用できる。リダイレクト先は使わない。登録欄には `https://localhost` を設定する。
- APIへは `Authorization: Bearer ...` と `Client-Id: ...` を送る。
- Client Secretやアクセストークンは、ソース・HTML・Git履歴・ログに含めない。チャットに貼り付ける必要もない。

次の実データ検証では、まず1ゲームの名前と配信の視聴者数を確認し、続いて上位最大1,000配信の取得・集計を検証する。
429にはレート制限のレスポンスヘッダーを利用して対応する。所要時間・必要なAPI呼び出し数は実測する。

## 急上昇3選

今回参照したゲーム・配信一覧APIは現在の情報を取得するものであり、過去との比較値は自分で保存する必要がある。
比較対象の日時と増加人数・増加率を明示する。初回や過去に未取得のゲームは「比較データなし」とし、0人から増えたと見なさない。
条件を満たすタイトルが3件未満の場合は、該当数だけ表示する。
現在の比較定義はREADME.mdに記載する。

## 定期実行とGitHubへの保存（追加要件）

2026-11-04の追加要件に従い、実行場所はこのLinux環境とする。GitHub Actionsで収集する案は採用しない。

確定した要件:

- 毎日19:00〜23:00に定期取得する。時刻は日本時間（Asia/Tokyo）で扱う。
- 1回ごとに取得結果を記録し、HTMLとともにcommit・pushする。
- 21:00・23:00の回では、前日の集計との比較結果もHTMLに出力してpushする。
- Twitchの認証情報はこの環境のGit管理外の設定ファイルに保存し、pushしない。

設計方針:

- 取得間隔は1時間。毎日19・20・21・22・23時の計5回とする。
- 21:00の回は当日19:00〜21:00と前日19:00〜21:00、23:00の回は両日の19:00〜23:00を比較する。
- 比較値はゲーム別の平均同時視聴者数とし、増加人数・増加率を併記する。人数の単純な足し算を期間中の視聴者数とは呼ばない。
- 比較には両日で一致する予定時刻の有効な記録を使い、利用件数・欠測件数を明示する。部分的な比較はその旨を表示し、有効な組がなければ比較しない。
- 取得範囲外や取得失敗のゲームは未取得として扱い、0人で補完しない。ゲームごとの比較可能件数も示す。
- 急上昇3選は比較可能なタイトルのうち増加人数が正のものを対象にする案とする。
- SQLiteを履歴の正本とし、日付・予定時刻付きのJSONをGitHub向けに出力する。最新HTMLと21時・23時の比較HTMLを生成する。通常処理でJSONを読み込む方式は廃止する。
- 時刻は収集開始予定時刻を指す。pushは取得・集計完了後となるため、21時・23時ちょうどの完了は保証しない。
- 重複実行を防ぐ。push失敗時も取得済み記録を保持し、次回に再送する。無関係なファイルはcommitせず、force pushは行わない。

この環境のsystemd timerを候補とし、初回設定後は自動で実行する。PCが停止・スリープしている間の過去データは後から取得できないため、実行時間中は稼働が必要。
systemctlは存在するが、この作業セッションからユーザーサービスへ接続すると `Operation not permitted` となった。定期実行の登録・動作確認は未実施。
push先は https://github.com/Phrixos-git/Research-viewer.git 。GitHub接続ツールでは空の公開Repositoryとして確認できた。シェルからの接続はDNS制限があり、定期処理で使うGit認証・疎通は未確認。

初回設定手順と実行用の入口を用意し、日常のターミナル操作を不要にする。認証済み実取得とタイマー稼働確認は、実装の単体テストとは分けて確認する。

## 公式資料

- [Get Top Games](https://dev.twitch.tv/docs/api/reference/#get-top-games)
- [Get Streams](https://dev.twitch.tv/docs/api/reference/#get-streams)
- [アプリ登録](https://dev.twitch.tv/docs/authentication/register-app/)
- [Client credentials grant flow](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow)
- [レート制限](https://dev.twitch.tv/docs/api/guide/#twitch-rate-limits)
