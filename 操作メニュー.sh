#!/usr/bin/env bash
set -u
cd -- "$(dirname -- "$0")" || exit 1
while true; do
  printf '\nResearch Viewer\n1. Twitchの認証情報を設定\n2. Twitchとの接続を確認\n3. 毎日19〜23時の自動実行を開始\n4. 最新のHTMLを開く\n5. 自動実行の状態を見る\n6. 自動実行を停止\n7. JSONから空のSQLiteへ履歴を復元\n8. Gmailの送信設定\n9. メール送信状況を見る\n0. メニューを閉じる\n番号: '
  read -r choice || exit 0
  case "$choice" in
    1) python3 viewer.py setup ;;
    2) python3 viewer.py verify ;;
    3) python3 viewer.py install-timer ;;
    4) python3 viewer.py open ;;
    5) systemctl --user status research-viewer.timer --no-pager ;;
    6) systemctl --user disable --now research-viewer.timer ;;
    7) python3 viewer.py restore ;;
    8) python3 viewer.py setup-mail ;;
    9) python3 viewer.py mail-status ;;
    0) exit 0 ;;
    *) printf '表示された番号を入力してください。\n' ;;
  esac
done
