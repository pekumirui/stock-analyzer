#!/usr/bin/env bash
# プライベートリポジトリを git fetch できるよう、stock の GitHub deploy key と origin を SSH にする。
# 公開鍵は GitHub → Settings → Deploy keys に read-only で登録する。
# 既存 VPS は ~/.ssh/id_github + Host github.com の config を使う。
set -euo pipefail

APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
SSH_DIR="${HOME}/.ssh"
REPO_SSH="${REPO_SSH:-git@github.com:pekumirui/stock-analyzer.git}"

install -d -m 700 "$SSH_DIR"

# 既にある GitHub 専用鍵を優先。無ければ作る。
if [[ -f "${SSH_DIR}/id_github" ]]; then
  KEY="${SSH_DIR}/id_github"
elif [[ -f "${SSH_DIR}/id_ed25519" ]]; then
  KEY="${SSH_DIR}/id_ed25519"
else
  KEY="${SSH_DIR}/id_github"
  ssh-keygen -t ed25519 -C "stock-analyzer-vps-github" -f "$KEY" -N ""
  echo "generated $KEY"
fi
chmod 600 "$KEY"
chmod 644 "${KEY}.pub"
echo "key $KEY"

CFG="${SSH_DIR}/config"
touch "$CFG"
chmod 600 "$CFG"
if ! grep -qE '^Host github\.com$' "$CFG" 2>/dev/null; then
  {
    echo "Host github.com"
    echo "  IdentityFile $KEY"
    echo "  IdentitiesOnly yes"
  } >> "$CFG"
  echo "wrote $CFG"
fi

KNOWN="${SSH_DIR}/known_hosts"
touch "$KNOWN"
chmod 644 "$KNOWN"
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
github_keys() {
  cat <<'EOF'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
EOF
}
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  fp="${line#github.com }"
  if ! grep -qF "$fp" "$KNOWN"; then
    printf '%s\n' "$line" >> "$KNOWN"
  fi
done < <(github_keys)

if [[ -n "${ADMIN_SSH_PUBKEY:-}" ]]; then
  AUTH="${SSH_DIR}/authorized_keys"
  touch "$AUTH"
  chmod 600 "$AUTH"
  if ! grep -qxF "$ADMIN_SSH_PUBKEY" "$AUTH"; then
    printf '%s\n' "$ADMIN_SSH_PUBKEY" >> "$AUTH"
    echo "added ADMIN_SSH_PUBKEY to authorized_keys"
  fi
fi

if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" remote set-url origin "$REPO_SSH"
  echo "origin $(git -C "$APP_DIR" remote get-url origin)"
fi

echo "DEPLOY_PUBKEY_BEGIN"
cat "${KEY}.pub"
echo "DEPLOY_PUBKEY_END"
