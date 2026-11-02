# Markdown wiki（MkDocs + Cloudflare Pages）

`docs/` の Markdown と、リポジトリ内のすべての `AGENTS.md` を GitHub を開かずに読む。元の md の配置は動かさない。ビルド時に `.wiki-docs/` へ集め、MkDocs Material で静的サイトにする。公開は Cloudflare Pages の Direct Upload。既存の VPS CI/CD には載せない。kabukpi.com の `/app` ナビから新しいタブで開く（公開月次には出さない）。

## 何を載せるか

載せる:

- `docs/` 配下の md。ホームは `docs/index.md`（無ければビルド時に仮の `index.md` を置く）。コピー時に `docs/` プレフィックスを外すので、wiki のトップになる
- リポジトリ内のすべての `AGENTS.md`（ルート、`app/api/AGENTS.md`、`static/AGENTS.md` など）。パスはそのまま

載せない:

- `.cursor/` と `.claude/`（中の `AGENTS.md` も含む）
- ルートの `README.md` / `DESIGN.md`
- `analyzer/` / `deploy/` など、上記以外の md
- `.venv` / `node_modules` / `data/` などの生成物・依存物

wiki に無いファイルへの相対リンクは **コピー側だけ** 外す（元の md は触らない）。`--strict` は wiki 内の壊れたリンクだけ落とす。`docs/index.md` の `../AGENTS.md` は GitHub 上ではリポジトリ直下へ向かい、wiki では `AGENTS.md` に付け替える。

## ローカル

アプリの `.venv` に wiki 用の依存を足してよい（`requirements.txt` には入れない）。ポート 8000 は SPA 用なので触らない。

```bash
.venv/bin/pip install -r requirements-wiki.txt
.venv/bin/mkdocs build --strict
.venv/bin/mkdocs serve -a 127.0.0.1:8001
```

`mkdocs build` が `tools/wiki/mkdocs_hook.py` で Markdown を集める（`docs/` の画像と HTML も。HTML は自動生成の `docs/dataflow/viewer.html`）。mermaid の図は material が描く（mermaid.js は CDN から読む）。`.wiki-docs/` と `site/` は gitignore。

## Cloudflare Pages プロジェクト（Direct Upload）

Git 連携は使わない。Git 連携で作ると GitHub 上のビルドと二重になり、あとから Direct Upload に切り替えられない。

1. [Workers & Pages](https://dash.cloudflare.com/?to=/:account/workers-and-pages) → **Create** → **Pages**
2. **Direct Upload**（Upload assets / Drag and drop）。Git リポジトリはつながない
3. プロジェクト名は GitHub の variable と同じにする（例: `stock-analyzer-wiki`）。URL は `<名前>.pages.dev`
4. Production branch は `main`（ダッシュボードで聞かれたら）。Direct Upload は後から branch を変える UI が無いので、違ったら [Update Project API](https://developers.cloudflare.com/pages/get-started/direct-upload/#production-branch-configuration) で `main` にする

空のまま作ってよい。中身は GitHub Actions の `wrangler pages deploy` が上げる。手元で作るなら:

```bash
npx wrangler pages project create
npx wrangler pages deploy site --project-name '<名前>'
```

手順の正本は [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/) と [CI からの Direct Upload](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)。

## API トークン（最小権限）

1. [API Tokens](https://dash.cloudflare.com/?to=/:account/api-tokens) → **Create Token** → Custom Token の **Get started**
2. 権限は **Account** / **Cloudflare Pages** / **Edit** だけ。Workers 一式や Zone の DNS は付けない
3. Account Resources はこのアカウントだけ。Zone Resources は不要なら付けない
4. 作った値は画面を閉じると再表示できない。GitHub にだけ入れる

Account ID はダッシュボード右サイドバーの **Account ID**（Workers & Pages の概要でも見える）。

## GitHub への登録

Repository → **Settings** → **Secrets and variables** → **Actions**。

| 種類 | 名前 | 値 |
|---|---|---|
| Secret | `CLOUDFLARE_API_TOKEN` | 上のトークン |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Account ID |
| Variable | `CLOUDFLARE_PAGES_PROJECT` | Pages のプロジェクト名 |

未設定の間は `.github/workflows/wiki.yml` がビルドだけしてデプロイを飛ばす（失敗にしない）。既存の `CI/CD` workflow（self-hosted）は触らない。wiki は `ubuntu-latest`。

`main` へ `docs/` / `AGENTS.md` / `mkdocs.yml` / `tools/wiki/` / この workflow が入ったときだけ動く。

## Cloudflare Access（本人のメールだけ）

`*.pages.dev` の本番も、プレビュー（`<hash>.<project>.pages.dev`）も、初期状態では誰でも読める。設計・運用の md が入るので Access で閉じる。

**`*.pages.dev` 全体を Access のホストにしない。** 他アカウントの Pages まで巻き込む。守るのは次の 2 つ。

- `<project>.pages.dev`（本番）
- `*.<project>.pages.dev`（プレビュー）

Cloudflare の Pages 設定 **Enable access policy** はプレビューだけを守る。本番の `<project>.pages.dev` は残る。両方を守る手順（[Known issues](https://developers.cloudflare.com/pages/platform/known-issues/#enable-access-on-your-pagesdev-domain)）:

1. Zero Trust でログイン方法を足す。メールにコードを送るなら **Settings** → **Authentication** → **One-time PIN**
2. Pages プロジェクト → **Settings** → **General** → **Enable access policy**（プレビュー用の Application ができる）
3. その Access を **Manage** → Application を **Configure**
4. **Public hostname** の Subdomain から `*` を消して保存。これで `<project>.pages.dev` が Access の後ろに入る。Application 名が被ったら変える
5. Pages の **Enable access policy** をもう一度押す。プレビュー用 `*.<project>.pages.dev` が再作成される
6. Zero Trust → **Access** → **Applications** に Application が 2 つあることを確認する（本番とプレビュー）
7. 両方のポリシーを **Allow**、Include は **Emails** に本人のアドレスだけ。アカウントメンバー全員やドメイン一致は使わない
8. カスタムドメインを足すときは、そのホスト用の Application を別に作る。無いとログイン画面だけ出て入れない

確認: 別ブラウザ（未ログイン）で本番 URL と、あればプレビュー URL を開き、どちらも Access の画面になること。
