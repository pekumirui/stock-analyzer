---- MODULE ActualsPick ----
EXTENDS Naturals, Sequences

CONSTANTS View, MaxLen,
          AllowPartialXbrl, AllowPdfAfterXbrl, AllowBigPartialPdf, AllowOrphanPartial,
          AllowPartialRow, AllowSameDay

VARIABLE seq

vars == <<seq>>

Items == {"a", "b"}
Values == {"v1", "v2"}
Absent == "absent"
FullRows == 10
BigRows == 8
SmallRows == 2
Slots == 1..MaxLen
Parts == {{"a"}, {"b"}}

Filings ==
    [src : {"edinet", "tdnet", "pdf"}, has : {Items}, rows : {FullRows}, val : Values, slot : {0}]
    \cup [src : {"edinet", "tdnet"}, has : Parts, rows : {SmallRows}, val : Values, slot : {0}]
    \cup [src : {"pdf"}, has : Parts, rows : {BigRows, SmallRows}, val : Values, slot : {0}]

Rows == [src : {"row"}, has : {Items} \cup Parts, rows : {0}, val : Values, slot : Slots]

Disclosures ==
    [src : {"tanshin"}, has : {{"a"}}, rows : {0}, val : Values, slot : Slots]
    \cup [src : {"tanshin"}, has : {{}}, rows : {0}, val : {Absent}, slot : Slots]
    \cup [src : {"edinet"}, has : {{"a"}}, rows : {0}, val : Values, slot : Slots]

Records ==
    CASE View = "three" -> Filings
      [] View = "row" -> Rows
      [] View = "gross" -> Disclosures

Max(S) == CHOOSE x \in S : \A y \in S : y <= x
Min(S) == CHOOSE x \in S : \A y \in S : x <= y

Of(s, src) == {i \in 1..Len(s) : s[i].src = src}
Stating(s, I, k) == {i \in I : k \in s[i].has}
LastStated(s, I, k) == IF Stating(s, I, k) = {} THEN Absent ELSE s[Max(Stating(s, I, k))].val
Own(s, i, k) == IF k \in s[i].has THEN s[i].val ELSE Absent

Tanshin(s) == Of(s, "tdnet") \cup Of(s, "pdf")

XbrlCode(s, src, k) ==
    IF Of(s, src) = {} THEN Absent ELSE Own(s, Max(Of(s, src)), k)

PdfCode(s, k) ==
    LET I == Of(s, "pdf") IN
    IF I = {} THEN Absent
    ELSE LET most == Max({s[i].rows : i \in I})
             base == Max({i \in I : s[i].rows * 10 >= most * 7})
             later == {i \in I : i > base}
         IN IF Stating(s, later, k) # {} THEN LastStated(s, later, k) ELSE Own(s, base, k)

ThreeCode(s, k) ==
    LET e == XbrlCode(s, "edinet", k)
        t == XbrlCode(s, "tdnet", k)
    IN IF e # Absent THEN e ELSE IF t # Absent THEN t ELSE PdfCode(s, k)

ThreeSpec(s, k) ==
    LET y == LastStated(s, Of(s, "edinet"), k)
    IN IF y # Absent THEN y ELSE LastStated(s, Tanshin(s), k)

RowCode(s, k) ==
    IF Len(s) = 0 THEN Absent
    ELSE LET top == Max({s[i].slot : i \in 1..Len(s)})
         IN Own(s, Min({i \in 1..Len(s) : s[i].slot = top}), k)

RowSpec(s, k) == LastStated(s, 1..Len(s), k)

FirstEdinet(s) == IF Of(s, "edinet") = {} THEN Absent ELSE s[Min(Of(s, "edinet"))].val

GrossCode(s, i) ==
    LET upto == {j \in Of(s, "tanshin") : s[j].slot <= s[i].slot} IN
    IF "a" \in s[i].has THEN s[i].val
    ELSE IF Stating(s, upto, "a") # {} THEN LastStated(s, upto, "a")
    ELSE FirstEdinet(s)

GrossSpec(s, i) ==
    LET upto == {j \in Of(s, "tanshin") : j <= i} IN
    IF Stating(s, upto, "a") # {} THEN LastStated(s, upto, "a") ELSE FirstEdinet(s)

Lineage(s, r) == IF r.src = "edinet" THEN Of(s, "edinet") ELSE Tanshin(s)

CanAppendFiling(s, r) ==
    /\ r.src # "pdf" /\ r.has # Items => AllowPartialXbrl
    /\ r.src = "pdf" /\ Of(s, "tdnet") # {} => AllowPdfAfterXbrl
    /\ r.rows = BigRows => AllowBigPartialPdf
    /\ (r.has # Items /\ ~(\E j \in Lineage(s, r) : s[j].has = Items)) => AllowOrphanPartial

CanAppendDated(s, r) ==
    /\ Len(s) > 0 => s[Len(s)].slot <= r.slot
    /\ (\E j \in 1..Len(s) : s[j].slot = r.slot /\ s[j].src = r.src) => AllowSameDay
    /\ r.src = "row" /\ r.has # Items => AllowPartialRow

CanAppend(s, r) == IF View = "three" THEN CanAppendFiling(s, r) ELSE CanAppendDated(s, r)

Init == seq = <<>>

Append1 ==
    /\ Len(seq) < MaxLen
    /\ \E r \in Records :
         /\ CanAppend(seq, r)
         /\ seq' = Append(seq, r)

Spec == Init /\ [][Append1]_vars

TypeOK == seq \in Seq(Records) /\ Len(seq) <= MaxLen

Matches ==
    CASE View = "three" -> \A k \in Items : ThreeCode(seq, k) = ThreeSpec(seq, k)
      [] View = "row" -> \A k \in Items : RowCode(seq, k) = RowSpec(seq, k)
      [] View = "gross" -> \A i \in Of(seq, "tanshin") : GrossCode(seq, i) = GrossSpec(seq, i)

====
