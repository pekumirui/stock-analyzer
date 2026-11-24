---- MODULE ForecastOverlay ----
EXTENDS Naturals, Sequences

CONSTANTS Item, MaxLen,
          AllowOlderReport, AllowBlankCorrection, AllowCorrectionAfterRevision,
          AllowStaleRevision, AllowUnmarkedWithdrawal

VARIABLE seq

vars == <<seq>>

Years == {1, 2}
Quarters == {2, 4}
Slots == 1..MaxLen
Values == {"v1", "v2"}
QuarterValue == "vq"
None == "none"
Blank == "blank"
Withdraw == "withdraw"
Untouched == "untouched"
NoKey == <<0, 0>>

Originals ==
    [kind : {"report"}, form : {"original"}, year : Years, q : Quarters,
     intent : Values \cup {Withdraw}, marked : {FALSE}, slot : Slots]

Corrections ==
    [kind : {"report"}, form : {"restate", "blank"}, year : Years, q : Quarters,
     intent : {Untouched}, marked : {FALSE}, slot : Slots]

Revisions ==
    {r \in [kind : {"revision"}, form : {"-"}, year : Years, q : {0},
            intent : Values \cup {Withdraw, Untouched}, marked : BOOLEAN, slot : Slots] :
        r.marked => r.intent = Withdraw}

Flashes ==
    IF Item = "perf"
    THEN [kind : {"flash"}, form : {"-"}, year : Years, q : {0},
          intent : {Untouched}, marked : {FALSE}, slot : Slots]
    ELSE {r \in [kind : {"flash"}, form : {"-"}, year : Years, q : {0},
                 intent : Values \cup {Withdraw, Untouched}, marked : BOOLEAN, slot : Slots] :
             r.marked => r.intent = Withdraw}

Records == Originals \cup Corrections \cup Revisions \cup Flashes

Key(r) == <<r.year, r.q>>
Target(r) == IF r.kind = "report" /\ r.q = 4 THEN r.year + 1 ELSE r.year
Older(k, l) == k[1] < l[1] \/ (k[1] = l[1] /\ k[2] < l[2])
Newest(k, l) == IF Older(k, l) THEN l ELSE k
IsOriginalOf(o, r) == o.kind = "report" /\ o.form = "original" /\ Key(o) = Key(r)

Stated(x) == IF x \in Values THEN x ELSE Blank

OriginalObs(s, r) ==
    LET j == CHOOSE j \in 1..Len(s) : IsOriginalOf(s[j], r)
    IN Stated(s[j].intent)

Obs(s, i) ==
    LET r == s[i] IN
    CASE r.kind = "report" /\ r.form = "original" -> Stated(r.intent)
      [] r.kind = "report" /\ r.form = "restate" -> OriginalObs(s, r)
      [] r.kind = "report" /\ r.form = "blank" -> Blank
      [] r.kind = "flash" /\ Item = "perf" -> QuarterValue
      [] OTHER -> Stated(r.intent)

ValueOf(o) == IF o = Blank THEN None ELSE o

ReadsNonReport(r) == Item = "div" \/ r.kind = "revision"

CodeStep(st, s, i) ==
    LET r == s[i]
        o == Obs(s, i)
    IN IF r.kind = "report"
       THEN IF Older(Key(r), st.latest)
            THEN [val |-> IF Item = "div" THEN ValueOf(o) ELSE st.val, latest |-> st.latest]
            ELSE [val |-> ValueOf(o), latest |-> Newest(Key(r), st.latest)]
       ELSE [val |-> CASE r.marked /\ ReadsNonReport(r) -> None
                       [] o # Blank /\ ReadsNonReport(r) -> o
                       [] OTHER -> st.val,
             latest |-> st.latest]

CodeFold(s) ==
    LET F[i \in 0..Len(s)] ==
            IF i = 0 THEN [val |-> None, latest |-> NoKey] ELSE CodeStep(F[i - 1], s, i)
    IN F[Len(s)].val

Apply(x, v) == CASE x \in Values -> x
                 [] x = Withdraw -> None
                 [] OTHER -> v

SpecIntent(st, r) ==
    CASE r.kind = "report" ->
            IF r.form = "original" /\ ~Older(Key(r), st.seen) THEN r.intent ELSE Untouched
      [] r.kind = "flash" /\ Item = "perf" -> Untouched
      [] OTHER -> r.intent

SpecStep(st, r) ==
    LET t == Target(r)
        x == SpecIntent(st, r)
        seen == IF r.kind = "report" THEN Newest(Key(r), st.seen) ELSE st.seen
    IN CASE t > st.target -> [target |-> t, val |-> Apply(x, None), seen |-> seen]
         [] t = st.target -> [target |-> t, val |-> Apply(x, st.val), seen |-> seen]
         [] OTHER -> [st EXCEPT !.seen = seen]

SpecState(s) ==
    LET F[i \in 0..Len(s)] ==
            IF i = 0 THEN [target |-> 0, val |-> None, seen |-> NoKey] ELSE SpecStep(F[i - 1], s[i])
    IN F[Len(s)]

Touches(st, r) == SpecIntent(st, r) # Untouched

TouchedSince(s, j, t) ==
    \E k \in (j + 1)..Len(s) :
        /\ s[k].kind # "report"
        /\ Target(s[k]) = t
        /\ Touches(SpecState(SubSeq(s, 1, k - 1)), s[k])

StaleTogether(a, b) ==
    \/ a.kind = "report" /\ b.kind # "report" /\ Target(a) > b.year
    \/ b.kind = "report" /\ a.kind # "report" /\ Target(b) > a.year

CanAppend(s, r) ==
    LET st == SpecState(s) IN
    /\ Len(s) > 0 => s[Len(s)].slot <= r.slot
    /\ \A k \in 1..Len(s) : s[k].slot = r.slot =>
         /\ ~(s[k].kind = "report" /\ r.kind = "report")
         /\ (Target(s[k]) = Target(r) /\ s[k].intent # Untouched /\ r.intent # Untouched)
              => s[k].intent = r.intent
    /\ (\E k \in 1..Len(s) : s[k].slot = r.slot /\ StaleTogether(s[k], r)) => AllowStaleRevision
    /\ r.kind = "report" /\ Older(Key(r), st.seen) => AllowOlderReport
    /\ r.kind = "report" /\ r.form = "original" =>
         ~\E k \in 1..Len(s) : IsOriginalOf(s[k], r)
    /\ r.kind = "report" /\ r.form # "original" =>
         \E j \in 1..Len(s) :
           /\ IsOriginalOf(s[j], r)
           /\ s[j].slot < r.slot
           /\ TouchedSince(s, j, Target(r)) => AllowCorrectionAfterRevision
    /\ r.kind = "report" /\ r.form = "blank" => AllowBlankCorrection
    /\ r.kind # "report" =>
         /\ st.target > 0
         /\ r.year <= st.target
         /\ r.year < st.target => AllowStaleRevision
         /\ r.intent = Withdraw /\ ~r.marked => AllowUnmarkedWithdrawal
         /\ (Touches(st, r) /\ \E k \in 1..Len(s) :
               s[k].slot = r.slot /\ s[k].form = "restate" /\ Target(s[k]) = Target(r))
              => AllowCorrectionAfterRevision

Init == seq = <<>>

Append1 ==
    /\ Len(seq) < MaxLen
    /\ \E r \in Records :
         /\ CanAppend(seq, r)
         /\ seq' = Append(seq, r)

Spec == Init /\ [][Append1]_vars

TypeOK == seq \in Seq(Records) /\ Len(seq) <= MaxLen

Matches == CodeFold(seq) = SpecState(seq).val

SlotOrders(s) ==
    {p \in [1..Len(s) -> 1..Len(s)] :
        /\ \A i, j \in 1..Len(s) : i # j => p[i] # p[j]
        /\ \A i \in 1..(Len(s) - 1) : s[p[i]].slot <= s[p[i + 1]].slot}

OrderIndependent ==
    \A p \in SlotOrders(seq) : CodeFold([i \in 1..Len(seq) |-> seq[p[i]]]) = CodeFold(seq)

====
