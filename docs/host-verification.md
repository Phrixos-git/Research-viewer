# ホストPCでの検証手順

PCの通常のターミナルで、上から順に実行してください。Codexの作業セッションでは通信が制限されるため、ホストPCで確認します。
各コード枠はまとめてコピーして実行できます。エラーが出た場合は次の手順へ進まず、表示内容を確認してください。

この手順にはGitHubへの実際のpushが含まれます。23時の通常処理では、設定済みの全宛先へ実際にメールを送ります。

## 1. フォルダーへ移動する

```bash
cd /home/Arietis/projects/research-viewer
python3 --version
```

Python 3.11以降であることを確認します。以降はこのフォルダーで実行します。
sudoは使いません。Client Secretやアプリパスワード、設定ファイルの内容はチャットに貼らないでください。

すでにタイマーを登録している場合は、手順7まで重複起動を避けるため一度停止します。未登録の場合はこの操作は不要です。

```bash
systemctl --user disable --now research-viewer.timer
```

実行中の取得処理はこの操作では停止しません。`systemctl --user is-active research-viewer.service` が `active` または `activating` の場合は完了を待ちます。

## 2. プログラムのテストとTwitchの接続確認

```bash
python3 -m unittest discover -s tests -q
python3 viewer.py verify
```

成功の目安:

- テストは最後に `OK` と表示される。
- Twitchは「認証と配信情報の取得に成功しました」と表示する。対象がゲームなら配信1件の視聴者数も表示する。

`verify` が最大1配信だけを確認するのは正常です。対象が雑談などの場合はゲーム配信なしと表示されます。上位最大1,000配信の集計やファイルへの保存は行いません。
テスト中に表示される過去の日付や保存メッセージは、一時フォルダー内の模擬データです。本番データや実メールではありません。

## 3. Gmailの認証だけを確認する（メール送信なし）

次をコード枠ごとコピーします。パスワードは保存済みファイルから読み込むため、コマンドへの入力は不要です。

```bash
python3 - <<'PY'
import smtplib
import ssl
from pathlib import Path
import mailer

try:
    config = mailer.load_settings(Path('.'))
    if config is None:
        raise mailer.MailError('メール通知が未設定または無効です。操作メニューの8で設定してください。')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30,
                          context=ssl.create_default_context()) as server:
        server.login(config['username'], config['app_password'])
        code, _ = server.noop()
        if code != 250:
            raise mailer.MailError('Gmailの応答を確認できませんでした。')
    print('Gmail認証成功。宛先数:', len(config['recipients']))
    print('メールは送信していません。')
except (mailer.MailError, OSError, ValueError):
    print('Gmail確認に失敗しました。ネットワーク、通知の有効化、送信用Gmail、アプリパスワードを確認してください。')
    raise SystemExit(1)
PY
```

「Gmail認証成功。宛先数: 2」などと表示されれば成功です。これは受信確認ではありません。

## 4. 上位最大1,000配信を実際に集計する（いつでも実行可・保存や送信なし）

Twitch全体の上位配信をページごとに取得するため、時間がかかる場合があります。取得中は完了メッセージが出るまで待ってください。

```bash
python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import viewer

try:
    print('上位最大1,000配信の集計を開始します。', datetime.now(viewer.JST).isoformat(), flush=True)
    result = viewer.client_from_config(Path('.')).collect()
    games = result['games']
    print('取得配信数:', result['stream_count'])
    print('ゲーム集計対象の配信数:', result['included_stream_count'])
    if games != sorted(games, key=lambda g: (-g['viewers'], g['id'])):
        raise viewer.ViewerError('視聴者数の並び順を確認できませんでした。')
    print('集計成功:', len(games), 'タイトル')
    for i, game in enumerate(games[:5], 1):
        print(f"{i}. {game['name']}: {game['viewers']:,}人")
    print('完了:', datetime.now(viewer.JST).isoformat())
    print('SQLite保存・HTML出力・push・メール送信は行っていません。')
except (viewer.ViewerError, OSError):
    print('上位最大1,000配信の集計に失敗しました。接続またはTwitch APIの状態を確認してください。')
    raise SystemExit(1)
PY
```

取得配信数が0〜1,000件となり、その範囲のゲーム別合計が表示されれば成功です。1,000件未満で終了しても正常です。ゲーム数は100固定ではありません。
この手順の取得は確認だけです。手順6では、その時点のデータを改めて取得・保存します。

## 5. GitHubへの保存を準備する

まず現在の状態を確認します。

```bash
git status --short --branch
git remote -v
```

Repository未作成で `not a git repository` と出る場合だけ、次を実行します。

```bash
git init -b main
git remote add origin https://github.com/Phrixos-git/Research-viewer.git
```

既存Repositoryの場合は、現在のブランチが `main`、originが上記URLであることを確認してください。既存の別ブランチや異なるリモートを、自動で上書きする手順ではありません。

```bash
git branch --show-current
git ls-remote --heads origin
git check-ignore .local/config.json .local/mail.json .local/viewers.sqlite3
```

- `git check-ignore` で3つのパスが表示されることを確認します。`.local/` はpushしません。
- `git ls-remote` が成功して何も表示しない場合は、リモートにブランチがない空のRepositoryです。
- リモートにすでにブランチがある場合は、ローカルと履歴が一致することを確認してから進めます。pushが拒否されてもforce pushはしません。

この開発で作成したファイルを、ファイル名を指定してcommitします。すでにステージ済みの別の変更がある場合は、先にその作業を整理してください。

```bash
git diff --cached --name-only
git add .gitignore README.md viewer.py mailer.py 操作メニュー.sh docs tests
git diff --cached --stat
git diff --cached --name-only
```

一覧が上記ソース・資料・テストだけであることを確認します。`.local/` や認証情報が含まれていたらcommitせず停止してください。
commitする変更がある場合のみ、次を実行します。

```bash
git commit -m "Implement Twitch viewer reports with SQLite and Gmail"
```

最初のpushでは、必要に応じてホストPC上でGitHubの認証を行います。

```bash
git push -u origin main
git status --short --branch
GIT_TERMINAL_PROMPT=0 git push --dry-run origin HEAD:refs/heads/main
```

最後のコマンドが認証入力を要求せず成功することを確認します。定期処理では手入力できないため、この確認が必要です。
`data/`・`output/` 以外の未commit変更が残っていると通常処理の自動commitが中断します。ソース修正などが残っていれば、この段階で整理してください。

## 6. 実際の収集・保存・HTML出力・pushを確認する

**日本時間の19:00〜19:09、20:00〜20:09、21:00〜21:09、22:00〜22:09、23:00〜23:09のいずれかで実行します。**
時刻外では「収集を見送ります」と表示され、収集しません。時計を変更したり、過去の日付を指定したりする必要はありません。
最初の確認は19時または20時を推奨します。23時に実行すると、設定済み宛先へ実メールも送ります。

```bash
date
python3 viewer.py collect
```

「記録をSQLiteへ保存し、JSONとHTMLを出力しました」と表示されれば、該当時刻の処理が成功しています。
pushや通知だけが失敗した場合も、SQLiteに保存された記録は残ります。次の確認で保存状況を調べてください。
同じ予定時刻の再実行では、保存済みデータを再利用し、成功済み宛先へのメールを重複送信しません。

保存済みの最新5回のタイトル数を、読み取り専用で確認します。

```bash
python3 - <<'PY'
import sqlite3
from pathlib import Path

path = Path('.local/viewers.sqlite3').resolve()
if not path.is_file():
    raise SystemExit('SQLiteがまだありません。収集の開始時刻と結果を確認してください。')
db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
try:
    rows = db.execute('''
        SELECT r.scheduled_at, COUNT(v.game_id), r.stream_count, r.included_stream_count
        FROM collection_runs r LEFT JOIN viewer_counts v ON v.run_id = r.id
        GROUP BY r.id ORDER BY r.scheduled_at DESC LIMIT 5
    ''').fetchall()
    if not rows:
        raise SystemExit('保存済みの取得記録はありません。')
    for stamp, count, streams, included in rows:
        print(stamp, 'タイトル数:', count, '取得配信数:', streams, '集計対象配信数:', included)
finally:
    db.close()
PY
```

各回の取得配信数が0〜1,000件で、集計対象配信数がそれ以下であることを確認します。タイトル数は一定ではありません。

```bash
python3 viewer.py open
```

ブラウザで取得範囲内のゲーム別合計が視聴者数の多い順に表示され、取得開始・終了時刻が表示されることを確認します。初回に「比較データなし」となるのは正常です。
GitHubで `data/日付/時刻.json` と `output/index.html` が更新されていることも確認します。SQLite本体と認証情報はGitHubに含まれないことを確認してください。

## 7. 定期実行を開始する

手順6が成功したら登録します。登録後は、同じ時刻に手動の `collect` を重ねて起動しないでください。

```bash
python3 viewer.py install-timer
systemctl --user is-enabled research-viewer.timer
systemctl --user is-active research-viewer.timer
systemctl --user list-timers research-viewer.timer --all --no-pager
```

`enabled`、`active` が表示され、次の実行予定が日本時間の19〜23時のいずれかになっていれば登録成功です。
PCは起動したまま、ユーザーセッションを維持し、収集・送信完了までスリープしないようにしてください。

```bash
journalctl --user -u research-viewer.service -n 40 --no-pager
```

各回の実行後に、保存メッセージと処理完了を確認します。未実行ならまだログがない場合があります。
サービスが実行後に `inactive (dead)` になること自体は正常です。毎回1回だけ処理して終了する方式です。タイマー側の `active` とログの成功を確認します。

## 8. 23時のメールと前日比較を確認する

23時の処理が完了してから実行します。

```bash
python3 viewer.py mail-status
```

設定した各宛先について `sent` になり、実際の受信箱で次を確認できれば成功です。

- 宛先ごとに個別のメールが届き、ほかの宛先は表示されない。
- HTML本文に全タイトル・急上昇3選・前日比較が表示される。
- `twitch-日付-23.html` が添付され、ブラウザで開ける。

`sent` はGmailが受け付けたことを表します。未着の場合は迷惑メールフォルダーも確認します。
`failed` は次の23時の処理で再試行されます。`sending`・`uncertain` は受付状況が不明なため、自動再送しません。状態を書き換えて再送する前に、送信済みメールや受信側を確認してください。

前日比較は、実際に2日間記録して確認します。初日は前日のデータがないので比較できません。

| 確認時刻 | 両日すべて取得した場合の比較件数 | 出力先 |
|---|---|---|
| 2日目の21時 | 19・20・21時の3時点 | `output/日付/21.html` |
| 2日目の23時 | 19〜23時の5時点 | `output/日付/23.html` とメール |

両日とも取得した配信に含まれるゲームなら3/3・5/5になります。途中でランキング外になったゲームや欠測がある場合は件数が少なくなります。
前日比較は同じゲームID・同じ時刻の平均人数を比べます。順位の上げ下げとは別です。

## 結果を共有するとき

以下があれば検証状況を判断できます。宛先アドレスや個人情報が表示される場合は伏せてください。

- 手順2〜4の成功・失敗メッセージ。
- 手順6のタイトル数と、HTMLの表示結果。
- GitHubへpushできたか。
- タイマーの `enabled`・`active` と次回実行時刻。
- 23時の各宛先の状態と、HTML本文・添付の受信結果。

設定ファイルの中身、アプリパスワード、Client Secret、アクセストークンは共有しないでください。
