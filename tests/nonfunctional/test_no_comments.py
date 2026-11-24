import json
import subprocess
import textwrap

from tools.lint.no_comments import REBUILD, count, diff, problems, scan, write


def _count(name, src):
    return count(name, textwrap.dedent(src))


def test_tree_matches_the_baseline():
    out = problems()
    assert not out, "\n".join(out)


def test_python_counts_comments_and_docstrings():
    src = '''\
        #!/usr/bin/env python3
        """module"""
        import os  # noqa: F401
        import sys  # noqa: E402  # 理由
        x = "# not a comment"  # trailing
        # own line
        def f():
            """doc"""
            return 1  # type: ignore[arg-type]
        class C:
            y = 1  # nosec B105
        '''
    assert _count("a.py", src) == {"comment": 3, "docstring": 2}


def test_python_without_comments_counts_nothing():
    assert _count("a.py", 'x = "#"\n\ndef f():\n    return "//"\n') == {}


def test_javascript_skips_strings_templates_and_regex():
    src = r'''
        const url = "https://example.com/a"; // one
        const t = `x // not ${ a /* two */ + `${b} // not` } // not`;
        const re = /\/\/[/*]/.test(s) ? 1 : 2;
        const d = a / b; /* three */
        const q = s.replace(/'/g, "//");
        '''
    assert _count("a.js", src) == {"comment": 3}


def test_css_counts_block_comments():
    assert _count("a.css", "a { color: red; } /* one */\n/* two\n   lines */\nb { content: '#'; }\n") == {"comment": 2}


def test_tla_counts_line_and_block_comments_but_not_operators():
    src = "---- MODULE M ----\nA == x \\in S /\\ y \\/ z \\* one\n(* two\n   lines *)\n====\n"
    assert _count("a.tla", src) == {"comment": 2}
    assert _count("a.cfg", "INVARIANT Inv\n\\* three\n") == {"comment": 1}


def test_shell_skips_shebang_directives_quotes_and_heredocs():
    src = '''\
        #!/usr/bin/env bash
        # shellcheck disable=SC2086
        # one
        echo "$# ${x#y} # not" '# not'  # two
        cat <<'EOF'
        # not
        EOF
        cat <<-EOT
        # not
        EOT
        echo done
        '''
    assert _count("a.sh", src) == {"comment": 2}


def test_diff_reports_more_and_fewer():
    more, fewer = diff({"a.py": {"comment": 2}, "c.py": {"docstring": 1}}, {"a.py": {"comment": 1}, "b.js": {"comment": 3}})
    assert more == ["a.py: comment 1 → 2", "c.py: docstring 0 → 1"]
    assert fewer == ["b.js: comment 3 → 0"]


def _repo(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_scan_reads_tracked_files_and_flags_unknown_extensions(tmp_path):
    root = _repo(tmp_path, {
        "app/a.py": "x = 1  # one\n",
        "app/xbrlp/v.py": "# vendor\n",
        "static/vendor/v.js": "// vendor\n",
        "docs/a.md": "# heading\n",
        "deploy/crontab": "# cron\n",
        "web/a.ts": "// one\n",
    })
    found, unknown = scan(root)
    assert found == {"app/a.py": {"comment": 1}}
    assert unknown == ["web/a.ts"]


def test_problems_follow_the_baseline(tmp_path):
    root = _repo(tmp_path, {"a.py": "x = 1  # one\n", "b.sh": "# one\n# two\n"})
    baseline = tmp_path / "baseline.json"
    write(root, baseline)
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"a.py": {"comment": 1}, "b.sh": {"comment": 2}}
    assert problems(root, baseline) == []
    (root / "a.py").write_text('"""doc"""\nx = 1  # one\n', encoding="utf-8")
    (root / "b.sh").write_text("# one\n", encoding="utf-8")
    out = problems(root, baseline)
    assert "  a.py: docstring 0 → 1" in out
    assert "  b.sh: comment 2 → 1" in out
    assert any(REBUILD in line for line in out)
