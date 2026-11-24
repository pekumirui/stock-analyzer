---- MODULE TdnetPdfRetire ----
EXTENDS Naturals

CONSTANT MaxZipReads

VARIABLES zip, pdf, zipReads, zipPc, batchPc, snapZipParsed,
          overlapped, retireFailed, batchSyncedLast

vars == <<zip, pdf, zipReads, zipPc, batchPc, snapZipParsed,
          overlapped, retireFailed, batchSyncedLast>>

ZipStatus == {"none", "parsed", "failed"}

Init ==
    /\ zip \in ZipStatus
    /\ pdf = (zip # "parsed")
    /\ zipReads = 0
    /\ zipPc = "idle"
    /\ batchPc = "idle"
    /\ snapZipParsed = FALSE
    /\ overlapped = FALSE
    /\ retireFailed = FALSE
    /\ batchSyncedLast = FALSE

ParseManifestRow ==
    /\ zipPc = "idle"
    /\ zipReads < MaxZipReads
    /\ \E result \in {"parsed", "failed"} :
         /\ zip' = result
         /\ zipPc' = IF result = "parsed" THEN "retire" ELSE "idle"
    /\ zipReads' = zipReads + 1
    /\ overlapped' = (overlapped \/ batchPc = "snapped")
    /\ batchSyncedLast' = FALSE
    /\ UNCHANGED <<pdf, batchPc, snapZipParsed, retireFailed>>

RetirePdfFiling ==
    /\ zipPc = "retire"
    /\ zipPc' = "idle"
    /\ pdf' = (zip # "parsed")
    /\ UNCHANGED <<zip, zipReads, batchPc, snapZipParsed, overlapped, retireFailed, batchSyncedLast>>

RetirePdfFilingRaises ==
    /\ zipPc = "retire"
    /\ zipPc' = "idle"
    /\ retireFailed' = TRUE
    /\ UNCHANGED <<zip, pdf, zipReads, batchPc, snapZipParsed, overlapped, batchSyncedLast>>

SyncPdfFilingsReadsZipSet ==
    /\ batchPc = "idle"
    /\ batchPc' = "snapped"
    /\ snapZipParsed' = (zip = "parsed")
    /\ UNCHANGED <<zip, pdf, zipReads, zipPc, overlapped, retireFailed, batchSyncedLast>>

SyncPdfFilingsSyncs ==
    /\ batchPc = "snapped"
    /\ batchPc' = "done"
    /\ pdf' = ~snapZipParsed
    /\ batchSyncedLast' = TRUE
    /\ UNCHANGED <<zip, zipReads, zipPc, snapZipParsed, overlapped, retireFailed>>

SyncPdfFilingsRaises ==
    /\ batchPc = "snapped"
    /\ batchPc' = "done"
    /\ UNCHANGED <<zip, pdf, zipReads, zipPc, snapZipParsed, overlapped, retireFailed, batchSyncedLast>>

Settled == zipPc = "idle" /\ batchPc \in {"idle", "done"}

Finished ==
    /\ Settled
    /\ zipReads = MaxZipReads
    /\ batchPc = "done"
    /\ UNCHANGED vars

Next ==
    \/ ParseManifestRow
    \/ RetirePdfFiling
    \/ RetirePdfFilingRaises
    \/ SyncPdfFilingsReadsZipSet
    \/ SyncPdfFilingsSyncs
    \/ SyncPdfFilingsRaises
    \/ Finished

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ zip \in ZipStatus
    /\ pdf \in BOOLEAN
    /\ zipReads \in 0..MaxZipReads
    /\ zipPc \in {"idle", "retire"}
    /\ batchPc \in {"idle", "snapped", "done"}

Both == zip = "parsed" /\ pdf

Neither == zip # "parsed" /\ ~pdf

RetiredWhenRunsDoNotOverlap ==
    (Settled /\ ~overlapped /\ ~retireFailed) => ~Both

BatchLeavesRightStateWhenRunLast ==
    (Settled /\ batchSyncedLast /\ ~overlapped) => (~Both /\ ~Neither)

NotBothAfterOverlap == (Settled /\ ~retireFailed) => ~Both

NotBothAfterRetireRaises == (Settled /\ ~overlapped) => ~Both

NeverNeither == Settled => ~Neither

====
