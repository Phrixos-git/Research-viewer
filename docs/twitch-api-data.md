# Twitch公式APIで追加取得できるデータの調査

調査日: 2026-11-18（JST）

## 結論と確認範囲

他人のチャンネルについて、現在と同じApp Access Tokenのまま追加で取れるのは、配信の付帯情報、VOD・クリップ、チャンネル・ユーザーの属性の3種類である。
チャット参加者・サブスク・Bitsなど視聴者の中身に関わるデータは、配信者またはモデレーターの許可が必要で、他人のチャンネルでは取れない。

確認したのは公式リファレンスの記載のみで、実際のAPI呼び出しは行っていない。チャットとEventSubのリファレンスは全文を確認できていない（下記「未確認事項」）。

## 現在使用しているAPI

- `GET /helix/streams`（`type=live`, `language=ja`）：`id`, `game_id`, `user_id`, `viewer_count`
- `GET /helix/games`：`id`, `name`, `igdb_id`
- 認証：Client Credentials（App Access Token）

## 1. 現在の応答に含まれるが未使用のフィールド

追加のAPI呼び出しは不要。

| エンドポイント | 未使用のフィールド | 用途の例 |
|---|---|---|
| `/streams` | `user_login`, `user_name`, `game_name`, `title`, `tags`, `started_at`, `language`, `thumbnail_url` | 配信者名の表示、配信時間の算出、タグ別の集計 |
| `/games` | `box_art_url` | ゲーム画像の表示 |

`/streams` の `tag_ids`（2023-02-28以降は常に空配列）と `is_mature`（常にfalse）は廃止済み。

## 2. App Access Tokenで呼べる他のエンドポイント

| エンドポイント | 取得できる主なデータ |
|---|---|
| `GET /games/top` | 全言語での人気カテゴリ順。視聴者数そのものは含まれない |
| `GET /videos` | VOD・ハイライト：`view_count`, `duration`, `stream_id`, `created_at`, `published_at` など。`id`・`user_id`・`game_id` のいずれか1つを指定。`user_id`/`game_id` 指定時は `period`（all/day/week/month）、`sort`（time/trending/views）、`type`、`language` で絞れる。`muted_segments` はApp Access Tokenでは常にnull |
| `GET /clips` | クリップ：`view_count`, `duration`, `creator_id`, `video_id`, `vod_offset`, `is_featured`。`broadcaster_id`・`game_id`・`id` のいずれか1つを指定し、`started_at`/`ended_at` で期間を絞れる |
| `GET /users` | `broadcaster_type`（partner/affiliate）、`type`、`created_at`、`description`、プロフィール画像。`view_count` は廃止済みで値が不正 |
| `GET /channels` | `broadcaster_language`, `game_name`, `title`, `tags`, `content_classification_labels`, `is_branded_content`, `delay`。オフラインでも取得できる。最大100件 |
| `GET /search/channels` | `is_live`、現在または最後に配信したゲーム、`started_at`。`live_only=true` ではカテゴリ名でも検索される |
| `GET /search/categories` | カテゴリ名の検索（`id`, `name`, `box_art_url`） |
| `GET /schedule` | 配信予定：開始・終了時刻、タイトル、カテゴリ、繰り返し、キャンセル、休暇モード。`/schedule/icalendar` は認証不要 |
| `GET /teams`, `GET /teams/channel` | チーム情報と所属メンバー |
| `GET /chat/emotes`, `/chat/emotes/global`, `/chat/badges`, `/chat/badges/global` | エモート・バッジの一覧 |
| EventSub `stream.online` / `stream.offline` | 配信開始・終了の通知。配信者の許可は不要。Webhookでの受信にはApp Access Tokenと公開HTTPSエンドポイントが必要 |

EventSubの購読は、配信者の許可が不要なもので1件あたりコスト1。Webhookの上限は応答例では `max_total_cost` 10000。WebSocketはユーザートークン専用で上限10。

## 3. ユーザートークンが必要だが配信者の許可は不要なもの

- `GET /channels/followers`：配信者・モデレーターでなくても、フォロワー総数（`total`）だけ返る（`data` は空）。App Access Tokenではなく、任意のユーザーのUser Access Tokenが必要。

## 4. 他人のチャンネルでは取得できないもの

配信者またはモデレーターの許可（スコープ）が必要。

- チャット参加者の一覧・人数（`/chat/chatters`、`moderator:read:chatters`）
- サブスク、Bitsランキング、Hype Train、投票、予想
- フォロワー一覧、広告スケジュール
- ゲーム・エクステンションの分析データ（所有者のみ）
- フォロー中チャンネル・フォロー中配信（`user:read:follows`、トークン本人の分のみ）

## 公式APIで提供されていないもの

- 過去の視聴者数の推移、総視聴時間、配信者ごとの平均視聴者数。自前で定期的に記録して算出する必要がある。

## 未確認事項

- チャット本文の取得（EventSub `channel.chat.message` やIRC）に必要な認証。チャンネルへの参加または配信者の許可が必要と考えられるが、該当ページを最後まで確認できていない。
- `channel.update`・`channel.raid` などstream以外のEventSub購読の認証要件。
- Get Chat Settings、Get Shared Chat Sessionの認証要件。

## 現在の集計への追加候補

`/streams` の `started_at` と `tags` を保存すれば、追加の通信なしで配信時間やタグ別の集計ができる。

## 公式資料

- [API Reference](https://dev.twitch.tv/docs/api/reference/)
- [Streams](https://dev.twitch.tv/docs/api/reference/streams) / [Games](https://dev.twitch.tv/docs/api/reference/games) / [Channels](https://dev.twitch.tv/docs/api/reference/channels) / [Videos](https://dev.twitch.tv/docs/api/reference/videos) / [Clips](https://dev.twitch.tv/docs/api/reference/clips) / [Users](https://dev.twitch.tv/docs/api/reference/users)
- [Search](https://dev.twitch.tv/docs/api/reference/search) / [Schedule](https://dev.twitch.tv/docs/api/reference/schedule) / [Teams](https://dev.twitch.tv/docs/api/reference/teams) / [Chat](https://dev.twitch.tv/docs/api/reference/chat)
- [EventSub stream](https://dev.twitch.tv/docs/eventsub/reference/stream) / [EventSub購読の管理](https://dev.twitch.tv/docs/eventsub/manage-subscriptions/)
