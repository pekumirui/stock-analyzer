# Markdown wiki のビルドと公開

`docs/` 配下の設計書・自動生成リファレンスとリポジトリ内の全 `AGENTS.md` を、元のファイル配置を変えずに MkDocs Material で検索付きの静的サイトにビルドし、Cloudflare Pages（Direct Upload）へ公開する仕組みと設定手順をまとめる。

本番の個人用ツール（`/app`）からはヘッダー右端の **wiki** リンクで別タブに開く（公開月次ページにはリンクを出さない）。

## 収録範囲とビルドの仕組み

`mkdocs build` を実行すると、フック `tools/wiki/mkdocs_hook.py` が一時ディレクトリ `.wiki-docs/`（出力先 `site/` とともに `.gitignore` 対象）へ対象ファイルを集め、ナビゲーション木と検索用レスポンスヘッダ `_headers`（`noindex, nofollow`）を生成する。

サイドバーは `docs/index.md` から組む。`##` の見出しが区分になり、その下に載せたリンクを載せた順に並べる。`docs/index.md` に載っていないページ（`schema/<機能>.md` など）は、載っているページのうち、載っていないページへのリンクが一番多いページ（`SCHEMA.md` など）の下にぶら下げる。どこからもリンクされていないページがあるとビルドが失敗するので、ページを足したら `docs/index.md` の表か、親にあたるページからリンクする。

| 区分 | 対象パス・ファイル | ビルド時の扱い |
|---|---|---|
| 収録する | `docs/` 配下の `.md` と静的アセット（画像、`docs/dataflow/viewer.html`、`docs/assets/mermaid-elk.js` など） | 先頭の `docs/` を外して `.wiki-docs/` 直下へ配置する（`docs/index.md` がトップページになる） |
| 収録する | リポジトリ内のすべての `AGENTS.md`（ルート、`app/api/AGENTS.md`、`static/AGENTS.md` など） | リポジトリルートからの相対パスを保ったまま配置する（`docs/index.md` からの `../AGENTS.md` リンクなども配置先に合わせて書き換える） |
| 収録しない | `.cursor/`・`.claude/`（内部の `AGENTS.md` を含む）、ルートの `README.md`、`analyzer/`・`deploy/` 等の `.md`、`.venv/`・`node_modules/`・`data/` | コピーしない。収録ページからこれらへの相対リンクがある場合は、**コピー側だけ**リンクを外してインラインコード表記（`` `../deploy/README.md` `` など）に置き換えるため、`mkdocs build --strict` では Wiki 内のリンク切れだけを厳密に検出できる |

あわせて `mkdocs build` の中で `tools/schema/erd.py` が走り、`docs/schema/tbls.json`（`python -m tools.schema.render` が `app/models.py` から生成）を入力に `npx --yes @liam-hq/cli@<LIAM> erd build --format tbls` を実行して `.wiki-docs/schema/erd/` に拡大・検索可能な ER 図バンドルを書き出す。Mermaid 図（ELK レイアウト含む）は `docs/assets/mermaid-elk.js` がブラウザ側で描画する。

## 手元でのビルドとプレビュー

Wiki 用の Python 依存パッケージはアプリの `requirements.txt` とは分けて `requirements-wiki.txt` に定義している。既存の `.venv` に追加インストールしてビルドする。なお Liam ERD の生成には Node.js（`npx`）が必要であり、`npx` が無い環境では ER 図が生成されず `--strict` ビルドが失敗する。また Liam ERD は `file://` 直開きでは動かないため、`http://` 経由で開く（ポート `8000` は本体 SPA が使うため `8001` を使う）。

```bash
.venv/bin/pip install -r requirements-wiki.txt
.venv/bin/mkdocs build --strict
.venv/bin/mkdocs serve -a 127.0.0.1:8001
```

## Cloudflare Pages（Direct Upload）と GitHub Actions の設定

既存の VPS 用 CI/CD（self-hosted ランナー）には載せず、`ubuntu-latest` 上の `.github/workflows/wiki.yml` でビルドして Cloudflare Pages へ Direct Upload する。Cloudflare 側で Git リポジトリ連携を選んでプロジェクトを作るとビルドが二重になり、後から Direct Upload へ切り替えられないため、必ず **Direct Upload** で作成する。

### 1. Cloudflare Pages プロジェクトの作成

1. Cloudflare ダッシュボードの [Workers & Pages](https://dash.cloudflare.com/?to=/:account/workers-and-pages) から **Create** > **Pages** > **Direct Upload** を選ぶ（Git リポジトリは接続しない）。
2. プロジェクト名（例: `stock-analyzer-wiki`、URL は `<名前>.pages.dev`）を決め、Production branch を聞かれたら `main` にする（後から UI では変更できないため、別名になった場合は [Update Project API](https://developers.cloudflare.com/pages/get-started/direct-upload/#production-branch-configuration) で `main` に直す）。中身は GitHub Actions の `wrangler pages deploy` が上げるため、作成時点では空のままでよい。

手元の CLI から空プロジェクトを作る場合は以下を実行する。

```bash
npx wrangler pages project create
npx wrangler pages deploy site --project-name '<プロジェクト名>'
```

### 2. 最小権限の API トークン発行と GitHub への登録

1. Cloudflare ダッシュボードの [API Tokens](https://dash.cloudflare.com/?to=/:account/api-tokens) から **Create Token** > Custom Token の **Get started** を選ぶ。
2. Permissions に **Account** / **Cloudflare Pages** / **Edit** のみを付与し（Workers や Zone DNS 権限は付けない）、Account Resources を対象アカウントだけに絞って発行する。あわせて右サイドバーから **Account ID** を控える。
3. GitHub リポジトリの **Settings** > **Secrets and variables** > **Actions** に以下の 3 つをすべて登録する。

| 種類 | 名前 | 設定値 |
|---|---|---|
| Repository secret | `CLOUDFLARE_API_TOKEN` | 発行した Cloudflare Pages Edit トークン |
| Repository secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare の Account ID |
| Repository variable | `CLOUDFLARE_PAGES_PROJECT` | Pages のプロジェクト名（例: `stock-analyzer-wiki`） |

3 つのうち 1 つでも未設定の間は、`.github/workflows/wiki.yml` は `mkdocs build --strict` の検査だけを実行し、デプロイ手順を警告付きでスキップする（ジョブ自体は失敗にしない）。`main` ブランチで `docs/**`・`**/AGENTS.md`・`mkdocs.yml`・`requirements-wiki.txt`・`tools/wiki/**`・`tools/schema/erd.py`・`.github/workflows/wiki.yml` が変更されたとき、または手動の `workflow_dispatch` で実行される。

## Cloudflare Access による閲覧制限

初期状態の `<project>.pages.dev` とプレビュー URL（`<hash>.<project>.pages.dev`）は誰でも閲覧できるため、Cloudflare Zero Trust（Access）で本人のメールアドレスだけに閉じる。

**注意:** Access の対象ホストに `*.pages.dev` 全体を指定すると他アカウントの Pages まで巻き込むため指定してはならない。また Pages 設定画面の **Enable access policy** 単体ではプレビュー URL しか保護されず、本番の `<project>.pages.dev` が公開のまま残る。公式の [Known issues 手順](https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain) に従い、以下の手順で本番とプレビューの両方を保護する。

1. Zero Trust ダッシュボードの **Settings** > **Authentication** でログイン方法（メール認証コードなら **One-time PIN**）を有効にする。
2. Pages プロジェクトの **Settings** > **General** で **Enable access policy** を押す（プレビュー用の Access Application が作られる）。
3. 作られた Access の **Manage** > Application の **Configure** を開き、**Public hostname** の Subdomain 欄から `*` を消して保存する（これで本番 `<project>.pages.dev` 用の Application になる。名前が衝突する場合は変更する）。
4. Pages 設定画面に戻り、もう一度 **Enable access policy** を押す（プレビュー `*.<project>.pages.dev` 用の Application が再作成される）。
5. Zero Trust の **Access** > **Applications** に本番用とプレビュー用の 2 つが並んでいることを確認し、両方のポリシーを Action `Allow`、Include ルール **Emails** = 本人のメールアドレスのみに設定する（アカウント全員やメールドメイン一括許可は使わない）。カスタムドメインを追加する場合は、そのホスト名用の Application も別途作成する。
6. 未ログインの別ブラウザ（シークレットウィンドウ）から本番 URL とプレビュー URL を開き、どちらも Access の認証画面に入ることと、ログイン後に検索・Mermaid 図・Liam ERD が動作することを確認する。
