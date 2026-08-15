let meta = #{
    name: "review-and-merge",
    description: "Review a topic branch vs main and fast-forward merge only if no confirmed blocking findings remain",
    when_to_use: "After implementation is committed on a topic branch and you want an independent review before merging to main",
    phases: [
        #{ title: "Inspect", detail: "confirm topic branch, clean tree, and commits ahead of main" },
        #{ title: "Review", detail: "parallel merge-blocking review" },
        #{ title: "Verify", detail: "independent skeptics on each finding" },
        #{ title: "Merge", detail: "fast-forward main only when the review is clean" },
    ],
};

let inspect_schema = #{
    "type": "object",
    "required": ["branch", "base", "dirty", "on_base", "has_diff", "can_review", "can_merge", "block_reason"],
    "properties": #{
        "branch": #{ "type": "string" },
        "base": #{ "type": "string" },
        "dirty": #{ "type": "boolean" },
        "on_base": #{ "type": "boolean" },
        "has_diff": #{ "type": "boolean" },
        "ahead": #{ "type": "integer" },
        "can_review": #{ "type": "boolean" },
        "can_merge": #{ "type": "boolean" },
        "block_reason": #{ "type": "string" },
    },
};

let findings_schema = #{
    "type": "object",
    "required": ["findings"],
    "properties": #{
        "findings": #{
            "type": "array",
            "maxItems": 8,
            "items": #{
                "type": "object",
                "required": ["file", "issue"],
                "properties": #{
                    "file": #{ "type": "string" },
                    "issue": #{ "type": "string" },
                },
            },
        },
    },
};

let verdict_schema = #{
    "type": "object",
    "required": ["real", "reason", "evidence"],
    "properties": #{
        "real": #{ "type": "boolean" },
        "reason": #{ "type": "string" },
        "evidence": #{ "type": "string" },
    },
};

let merge_schema = #{
    "type": "object",
    "required": ["merged", "sha", "pushed", "message"],
    "properties": #{
        "merged": #{ "type": "boolean" },
        "sha": #{ "type": "string" },
        "pushed": #{ "type": "boolean" },
        "message": #{ "type": "string" },
    },
};

fn in_repo(path) {
    if type_of(path) != "string" { return false; }
    if path.index_of(".venv") >= 0 { return false; }
    if path.index_of("__pycache__") >= 0 { return false; }
    if path.index_of("stock.db") >= 0 { return false; }
    true
}

let want_merge = true;
let want_push = false;
let base = "main";
let branch_arg = "";
if args != () {
    if args.merge == false { want_merge = false; }
    if args.push == true { want_push = true; }
    if args.base != () && type_of(args.base) == "string" && args.base != "" { base = args.base; }
    if args.branch != () && type_of(args.branch) == "string" && args.branch != "" { branch_arg = args.branch; }
}

phase("Inspect");
let inspect_prompt = "";
inspect_prompt += "Inspect this git repo and fill the schema from live commands. ";
inspect_prompt += "Do not guess. Run: git rev-parse --abbrev-ref HEAD, git status --porcelain, ";
inspect_prompt += "git rev-parse --verify ";
inspect_prompt += base;
inspect_prompt += ", git merge-base ";
inspect_prompt += base;
inspect_prompt += " HEAD, git rev-list --count ";
inspect_prompt += base;
inspect_prompt += "..HEAD, git diff --stat ";
inspect_prompt += base;
inspect_prompt += "...HEAD. ";
if branch_arg != "" {
    inspect_prompt += "The intended topic branch is ";
    inspect_prompt += branch_arg;
    inspect_prompt += ". If HEAD is not that branch, checkout that branch only if the working tree is clean; otherwise set can_review=false. ";
}
inspect_prompt += "Rules: dirty=true if porcelain is non-empty. on_base=true if HEAD branch equals ";
inspect_prompt += base;
inspect_prompt += ". has_diff=true if rev-list count > 0 or the triple-dot diff is non-empty. ";
inspect_prompt += "can_review=true only when dirty=false AND on_base=false AND has_diff=true. ";
inspect_prompt += "can_merge=true only when can_review=true. ";
inspect_prompt += "If can_review is false, set block_reason to one of: ";
inspect_prompt += "'on main; create a topic branch first', ";
inspect_prompt += "'uncommitted changes; commit first', ";
inspect_prompt += "'no commits ahead of main', or a short concrete git error. ";
inspect_prompt += "If can_review is true, set block_reason to an empty string.";

let inspected = agent(inspect_prompt, #{
    label: "inspect-git",
    agent_type: "explore",
    output_schema: inspect_schema,
});

if inspected == () || !inspected.success || inspected.output == () {
    complete(#{ merged: false, summary: "Inspect failed; nothing merged." });
}

let st = inspected.output;
if st.can_review != true {
    let why = st.block_reason;
    if why == () || why == "" { why = "branch is not reviewable"; }
    complete(#{
        merged: false,
        branch: st.branch,
        base: base,
        summary: why,
    });
}

let branch_name = st.branch;
if type_of(branch_name) != "string" || branch_name == "" {
    complete(#{ merged: false, summary: "Inspect did not return a branch name." });
}

phase("Review");
let dimensions = [
    "correctness bugs and broken API/UI contracts",
    "project-rule violations (J-Quants V2 only, adj_* prices, no demo/real mix, no extra layers, no hardcoded thresholds, no secrets)",
    "DESIGN.md drift, missing verification, or regressions in screener/statements/chart/demo-mix guard",
];

let review_jobs = [];
for d in dimensions {
    let p = "";
    p += "Review committed changes of branch ";
    p += branch_name;
    p += " against ";
    p += base;
    p += " for ";
    p += d;
    p += ". Use git (git diff ";
    p += base;
    p += "...";
    p += branch_name;
    p += " and git log --oneline ";
    p += base;
    p += "..";
    p += branch_name;
    p += ") plus read_file/grep on the changed files. Do not answer from memory. ";
    p += "Report at most 8 MERGE-BLOCKING findings as {file, issue}. ";
    p += "Merge-blocking means a real bug, a broken contract, a secret, mixing demo and real data, ";
    p += "using raw prices for analysis, calling J-Quants V1, adding a new layer/dependency, ";
    p += "or leaving DESIGN.md wrong after a behavior change. ";
    p += "Do NOT report nits, style, or optional cleanups — those must not block merge. ";
    p += "An empty list is valid only after you have read the actual diff.";
    review_jobs.push(#{
        prompt: p,
        label: "review:" + d,
        agent_type: "explore",
        output_schema: findings_schema,
    });
}
let review_results = parallel(review_jobs);

let findings = [];
for r in review_results {
    if r != () && r.success && r.output != () && r.output.findings != () {
        for f in r.output.findings {
            if f != () && f.file != () && f.issue != () && in_repo(f.file) {
                findings.push(f);
            }
        }
    }
}
log(findings.len().to_string() + " merge-blocking findings before verification");

if findings.len() == 0 {
    if !want_merge {
        complete(#{
            merged: false,
            branch: branch_name,
            base: base,
            summary: "Review clean; merge skipped because args.merge=false.",
            confirmed: [],
        });
    }
} else {
    phase("Verify");
    let vjobs = [];
    for f in findings {
        let vp = "";
        vp += "Adversarially verify this merge-blocking review finding by reading the shipped code on branch ";
        vp += branch_name;
        vp += ". Finding: \"";
        vp += f.issue;
        vp += "\" in ";
        vp += f.file;
        vp += ". Use git show / read_file / grep. Set real=true only with concrete evidence you independently inspected. ";
        vp += "Otherwise default real=false. Style nits and speculative concerns are real=false.";
        vjobs.push(#{
            prompt: vp,
            label: "verify:" + f.file,
            agent_type: "explore",
            output_schema: verdict_schema,
        });
    }
    let verdicts = parallel(vjobs);

    let confirmed = [];
    let unverified = 0;
    let i = 0;
    for v in verdicts {
        if v == () || !v.success || v.output == () {
            unverified += 1;
        } else if v.output.real == true && v.output.evidence != () && v.output.evidence != "" {
            confirmed.push(findings[i]);
        }
        i += 1;
    }
    log(confirmed.len().to_string() + " confirmed, " + unverified.to_string() + " unverified");

    if confirmed.len() > 0 || unverified > 0 {
        let summary = confirmed.len().to_string() + " confirmed / " + unverified.to_string() + " unverified findings; not merged.";
        let report = "# review-and-merge\n\n" + summary + "\n\nFix these on the topic branch and re-run.\n";
        let path = write_scratch_file("report.md", report);
        complete(#{
            path: path,
            merged: false,
            branch: branch_name,
            base: base,
            summary: summary,
            confirmed: confirmed,
            unverified: unverified,
        });
    }
}

if !want_merge {
    complete(#{
        merged: false,
        branch: branch_name,
        base: base,
        summary: "Review clean; merge skipped because args.merge=false.",
        confirmed: [],
    });
}

if st.can_merge != true {
    complete(#{
        merged: false,
        branch: branch_name,
        summary: "Review clean but inspect.can_merge was not true; not merged.",
    });
}

phase("Merge");
let merge_prompt = "";
merge_prompt += "Fast-forward merge topic branch into base. Use ONLY these git commands, in order, and nothing else. ";
merge_prompt += "Working directory is the repo root. PowerShell is fine. ";
merge_prompt += "1) git status --porcelain  (must be empty; if not, merged=false and stop) ";
merge_prompt += "2) git rev-parse --abbrev-ref HEAD  (must equal ";
merge_prompt += branch_name;
merge_prompt += "; if not, git checkout ";
merge_prompt += branch_name;
merge_prompt += " only when porcelain is empty) ";
merge_prompt += "3) git checkout ";
merge_prompt += base;
merge_prompt += " ";
merge_prompt += "4) git merge --ff-only ";
merge_prompt += branch_name;
merge_prompt += "  (if this fails, do not fall back to --no-ff; merged=false and stop) ";
if want_push {
    merge_prompt += "5) git push origin ";
    merge_prompt += base;
    merge_prompt += " ";
} else {
    merge_prompt += "5) Do not push. ";
}
merge_prompt += "6) git rev-parse HEAD  (put the sha in the schema). ";
merge_prompt += "Do not delete the topic branch. Do not amend. Do not force-push. Do not edit files.";

let merged = agent(merge_prompt, #{
    label: "ff-merge",
    capability_mode: "execute",
    output_schema: merge_schema,
});

if merged == () || !merged.success || merged.output == () || merged.output.merged != true {
    let msg = "Merge command failed.";
    if merged != () && merged.output != () && merged.output.message != () {
        msg = merged.output.message;
    }
    complete(#{
        merged: false,
        branch: branch_name,
        base: base,
        summary: msg,
    });
}

let sha = merged.output.sha;
let pushed = merged.output.pushed == true;
let summary = "Merged " + branch_name + " into " + base + " at " + sha;
if pushed { summary += " (pushed)"; }
let report = "# review-and-merge\n\n" + summary + "\n";
let path = write_scratch_file("report.md", report);
complete(#{
    path: path,
    merged: true,
    branch: branch_name,
    base: base,
    sha: sha,
    pushed: pushed,
    summary: summary,
    confirmed: [],
});
